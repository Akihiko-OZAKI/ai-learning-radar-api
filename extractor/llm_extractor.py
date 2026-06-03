"""
LLM 用語抽出モジュール

役割:
  1. 収集テキスト（GitHub description/topics + HN title）から
     辞書DBに未登録の AI 技術用語を発見する。
  2. 発見した用語にテーマ（theme）とカテゴリ（category）を付与する。
  3. 新規登録用語の説明文（100〜300文字）を生成する。

LLM の使用は上記3用途のみ。ランキング計算には一切使用しない。
"""

import json
import logging
import os
import textwrap
from datetime import date
from typing import Optional

from openai import OpenAI

from db import get_connection, get_raw_connection

logger = logging.getLogger(__name__)

client = OpenAI()  # OPENAI_API_KEY は環境変数から自動取得

# 使用モデル（コスト最小化のため小型モデルを使用）
LLM_MODEL = "gpt-4.1-mini"

# テーマ選択肢（プロンプトに埋め込む）
THEME_OPTIONS = [
    "llm",
    "ai_coding",
    "ai_agent",
    "tool_integration",
    "retrieval",
    "ai_infra",
    "multimodal",
    "ai_framework",
    "other",
]

# カテゴリ選択肢
CATEGORY_OPTIONS = ["Model", "Tool", "Framework", "Protocol", "Agent", "Library", "Other"]

# 1回の LLM 呼び出しに渡すテキスト件数（チャンクサイズ）
CHUNK_SIZE = 80


def _get_known_terms() -> set[str]:
    """辞書DBに登録済みの用語名（小文字）を返す。"""
    conn = get_connection()
    rows = conn.execute("SELECT term_name FROM terms").fetchall()
    conn.close()
    return {row["term_name"].lower() for row in rows}


def _get_theme_id(theme_key: str) -> Optional[int]:
    """theme_key から theme_id を返す。"""
    conn = get_connection()
    row = conn.execute(
        "SELECT theme_id FROM themes WHERE theme_key=?", (theme_key,)
    ).fetchone()
    conn.close()
    return row["theme_id"] if row else None


def _build_extraction_prompt(texts: list[str], known_terms: set[str]) -> str:
    """用語抽出プロンプトを構築する。"""
    known_sample = ", ".join(sorted(known_terms)[:30]) if known_terms else "（なし）"
    texts_block = "\n".join(f"- {t}" for t in texts)
    theme_list = ", ".join(THEME_OPTIONS)
    category_list = ", ".join(CATEGORY_OPTIONS)

    return textwrap.dedent(f"""
        あなたはAI技術トレンドの専門家です。
        以下のテキストリストから、AI技術に関連する固有の技術用語を抽出してください。

        ## 抽出ルール
        - 対象: LLM、AIモデル、AIツール、AIフレームワーク、AIエージェント関連技術、AIプロトコル、AI開発環境
        - 除外: 個人名、GitHubユーザー名、URL、一般名詞、ノイズワード、汎用的な英単語
        - 除外: 以下の既知用語（辞書登録済み）: {known_sample}
        - 1〜4単語程度の固有名詞・技術名のみ抽出すること

        ## 出力形式
        以下のJSON形式で返すこと:
        {{"terms": [
          {{"term": "用語名", "theme": "テーマキー", "category": "カテゴリ"}}
        ]}}

        - term: 用語名（英語表記を優先、例: "LangGraph", "MCP", "Claude Code"）
        - theme: テーマキー（{theme_list} のいずれか1つ）
        - category: カテゴリ（{category_list} のいずれか1つ）

        ## テキストリスト
        {texts_block}
    """).strip()


def _build_description_prompt(term: str) -> str:
    """用語の説明文生成プロンプトを構築する。"""
    return textwrap.dedent(f"""
        AI技術用語「{term}」について、日本語で100〜300文字の簡潔な説明文を書いてください。
        対象読者は日本人IT技術者・AI学習者です。
        説明文のみを返してください。
    """).strip()


def _extract_terms_from_texts(texts: list[str], known_terms: set[str]) -> list[dict]:
    """テキストリストから新規 AI 用語を LLM で抽出する。"""
    results = []
    for i in range(0, len(texts), CHUNK_SIZE):
        chunk = texts[i : i + CHUNK_SIZE]
        prompt = _build_extraction_prompt(chunk, known_terms)
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,  # 決定論的な出力
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or "{\"terms\":[]}"
            # JSON オブジェクト形式 {"terms": [...]} で返ってくる
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                # "terms" キーを優先して取得
                items = parsed.get("terms", [])
                if not items:
                    # フォールバック: 最初のリスト値を使う
                    for v in parsed.values():
                        if isinstance(v, list):
                            items = v
                            break
                results.extend(items)
            elif isinstance(parsed, list):
                results.extend(parsed)
        except Exception as e:
            logger.error(f"[LLM] Extraction error (chunk {i}): {e}")
    return results


def _generate_description(term: str) -> str:
    """用語の説明文を LLM で生成する。"""
    prompt = _build_description_prompt(term)
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=400,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"[LLM] Description generation error for '{term}': {e}")
        return ""


def _collect_texts_for_today() -> list[str]:
    """本日収集した GitHub・HN のテキストを結合して返す。"""
    today = str(date.today())
    conn = get_raw_connection()

    # GitHub: repo_name + description + topics
    gh_rows = conn.execute(
        "SELECT repo_name, description, topics FROM raw_github WHERE collected_at=?",
        (today,),
    ).fetchall()

    # HN: title
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
                topics = json.loads(row["topics"])
                parts.extend(topics)
            except Exception:
                pass
        texts.append(" | ".join(parts))

    for row in hn_rows:
        texts.append(row["title"])

    return texts


def run_extraction() -> int:
    """
    本日の収集データから新規 AI 用語を抽出し、terms テーブルに候補登録する。

    Returns:
        新規登録した用語数
    """
    today = date.today()
    known_terms = _get_known_terms()
    texts = _collect_texts_for_today()

    if not texts:
        logger.warning("[LLM] No texts to extract from. Skipping.")
        return 0

    logger.info(f"[LLM] Extracting terms from {len(texts)} texts...")
    extracted = _extract_terms_from_texts(texts, known_terms)

    # 重複排除（大文字小文字を無視）
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

            # 説明文を生成
            description = _generate_description(term_name)

            conn.execute(
                """
                INSERT OR IGNORE INTO terms
                    (term_name, theme_id, category, first_seen, last_seen,
                     description, is_permanent)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (term_name, theme_id, category, str(today), str(today), description),
            )
            registered += 1

    conn.close()
    logger.info(f"[LLM] Registered {registered} new terms.")
    return registered
