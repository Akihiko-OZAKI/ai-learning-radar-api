"""
LLM 用語抽出モジュール（Anthropic Claude版）

LLMの使用は「新規用語発見・テーマ付与・説明文生成」のみ。
ランキング計算には一切使用しない。
"""

import json
import logging
import textwrap
from datetime import date
from typing import Optional

import anthropic

from db import get_connection, get_raw_connection

logger = logging.getLogger(__name__)

client = anthropic.Anthropic()  # ANTHROPIC_API_KEY は環境変数から自動取得
LLM_MODEL = "claude-haiku-4-5"

THEME_OPTIONS = [
    "llm", "ai_coding", "ai_agent", "tool_integration",
    "retrieval", "ai_infra", "multimodal", "ai_framework", "other",
]
CATEGORY_OPTIONS = ["Model", "Tool", "Framework", "Protocol", "Agent", "Library", "Other"]
CHUNK_SIZE = 80


def _get_known_terms() -> set[str]:
    conn = get_connection()
    rows = conn.execute("SELECT term_name FROM terms").fetchall()
    conn.close()
    return {row["term_name"].lower() for row in rows}


def _get_theme_id(theme_key: str) -> Optional[int]:
    conn = get_connection()
    row = conn.execute("SELECT theme_id FROM themes WHERE theme_key=?", (theme_key,)).fetchone()
    conn.close()
    return row["theme_id"] if row else None


def _build_extraction_prompt(texts: list[str], known_terms: set[str]) -> str:
    known_sample = ", ".join(sorted(known_terms)[:40]) if known_terms else "（なし）"
    texts_block = "\n".join(f"- {t[:200]}" for t in texts)
    theme_list = ", ".join(THEME_OPTIONS)
    category_list = ", ".join(CATEGORY_OPTIONS)

    return textwrap.dedent(f"""
        あなたはAI技術トレンドの専門家です。
        以下のテキストから、現在注目されているAI技術の固有名詞・ツール名・モデル名・フレームワーク名のみを抽出してください。

        ## 抽出基準（厳格に適用）
        - 対象: LLM、生成AI、AIエージェント、AIコーディングツール、AIプロトコル、RAGシステム、マルチモーダルAI
        - 対象: 具体的な製品名・サービス名・技術名（例: Claude Code, LangGraph, MCP, Cursor, vLLM）
        - 除外: 汎用プログラミング言語（Python, JavaScript等）
        - 除外: 汎用インフラ（Docker, Kubernetes, AWS等）
        - 除外: 古典的MLアルゴリズム（CNN, RNN, LSTM等）
        - 除外: 既知用語: {known_sample}
        - 1〜4単語の固有名詞のみ

        ## 出力形式（JSONのみ、説明文不要）
        {{"terms": [
          {{"term": "用語名", "theme": "テーマキー", "category": "カテゴリ"}}
        ]}}
        テーマキー: {theme_list}
        カテゴリ: {category_list}

        ## テキスト
        {texts_block}
    """).strip()


def _extract_terms_from_texts(texts: list[str], known_terms: set[str]) -> list[dict]:
    results = []
    for i in range(0, len(texts), CHUNK_SIZE):
        chunk = texts[i:i + CHUNK_SIZE]
        prompt = _build_extraction_prompt(chunk, known_terms)
        try:
            message = client.messages.create(
                model=LLM_MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            content = message.content[0].text if message.content else '{"terms":[]}'
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                content = content[start:end]
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                items = parsed.get("terms", [])
                if not items:
                    for v in parsed.values():
                        if isinstance(v, list):
                            items = v
                            break
                results.extend(items)
            elif isinstance(parsed, list):
                results.extend(parsed)
            logger.info(f"[LLM] Chunk {i//CHUNK_SIZE+1}: extracted {len(results)} terms total")
        except Exception as e:
            logger.error(f"[LLM] Extraction error (chunk {i}): {e}")
    return results


def _generate_description(term: str) -> str:
    try:
        message = client.messages.create(
            model=LLM_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": f"AI技術用語「{term}」について、日本語で100〜200文字の簡潔な説明文を書いてください。説明文のみ返してください。"}],
        )
        return (message.content[0].text if message.content else "").strip()
    except Exception as e:
        logger.error(f"[LLM] Description error for '{term}': {e}")
        return ""


def _collect_texts_for_today() -> list[str]:
    today = str(date.today())
    conn = get_raw_connection()
    gh_rows = conn.execute(
        "SELECT repo_name, description, topics FROM raw_github WHERE collected_at=?", (today,)
    ).fetchall()
    hn_rows = conn.execute(
        "SELECT title FROM raw_hn WHERE collected_at=?", (today,)
    ).fetchall()
    conn.close()

    texts = []
    for row in gh_rows:
        parts = [row["repo_name"]]
        if row["description"]:
            parts.append(row["description"])
        if row["topics"]:
            try:
                parts.extend(json.loads(row["topics"]))
            except Exception:
                pass
        texts.append(" | ".join(parts))
    for row in hn_rows:
        texts.append(row["title"])
    return texts


def run_extraction() -> int:
    today = date.today()
    known_terms = _get_known_terms()
    texts = _collect_texts_for_today()

    if not texts:
        logger.warning("[LLM] No texts to extract from. Skipping.")
        return 0

    logger.info(f"[LLM] Extracting terms from {len(texts)} texts...")
    extracted = _extract_terms_from_texts(texts, known_terms)

    seen: set[str] = set(known_terms)
    new_terms = []
    for item in extracted:
        term_name = (item.get("term") or "").strip()
        if not term_name or term_name.lower() in seen:
            continue
        seen.add(term_name.lower())
        new_terms.append(item)

    logger.info(f"[LLM] {len(new_terms)} new term candidates found.")

    conn = get_connection()
    registered = 0
    with conn:
        for item in new_terms:
            term_name = item["term"].strip()
            theme_key = item.get("theme", "other")
            category = item.get("category", "Other")
            theme_id = _get_theme_id(theme_key)
            description = _generate_description(term_name)
            conn.execute(
                """INSERT OR IGNORE INTO terms
                    (term_name, theme_id, category, first_seen, last_seen, description, is_permanent)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (term_name, theme_id, category, str(today), str(today), description),
            )
            registered += 1

    conn.close()
    logger.info(f"[LLM] Registered {registered} new terms.")
    return registered
