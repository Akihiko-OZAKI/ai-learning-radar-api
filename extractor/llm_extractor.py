"""
LLM 用語抽出モジュール（Groq / OpenAI互換版）

LLMの使用は「新規用語発見・テーマ付与・説明文生成」のみ。
ランキング計算には一切使用しない。

対応LLMプロバイダー（環境変数で切り替え）:
  GROQ_API_KEY が設定されている場合 → Groq (llama-3.3-70b-versatile)
  OPENAI_API_KEY が設定されている場合 → OpenAI (gpt-4o-mini)
  どちらもない場合 → スキップ

TPD上限対策:
  既知用語名を含むテキストを除外し、未知テキストのみをLLMに投げる。
  処理量を大幅削減（約1/6〜1/8）してGroq無料枠のTPD上限を回避する。
"""

import json
import logging
import os
import textwrap
from datetime import date
from typing import Optional

from db import get_connection, get_raw_connection

logger = logging.getLogger(__name__)

# ── LLMクライアントの初期化（Groq優先） ────────────────────────
def _init_client():
    groq_key = os.environ.get("GROQ_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if groq_key:
        from groq import Groq
        logger.info("[LLM] Using Groq (llama-3.3-70b-versatile)")
        return Groq(api_key=groq_key), "llama-3.3-70b-versatile", "groq"
    elif openai_key:
        from openai import OpenAI
        logger.info("[LLM] Using OpenAI (gpt-4o-mini)")
        return OpenAI(api_key=openai_key), "gpt-4o-mini", "openai"
    else:
        logger.warning("[LLM] No API key found. LLM extraction will be skipped.")
        return None, None, None

client, LLM_MODEL, LLM_PROVIDER = _init_client()

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
        以下のテキストから、「AI技術そのもの」の固有名詞のみを厳格に抽出してください。

        ## 抽出対象（これらのみ）
        - LLMモデル名: GPT-4o, Claude 3.5, Llama, Gemini, Mistral, Qwen等
        - AIフレームワーク・ライブラリ: LangChain, LangGraph, PyTorch, Transformers等
        - AIプロトコル・規格: MCP, A2A, OpenAI API等
        - AI推論・サービングインフラ: vLLM, Ollama, Groq, Together AI等
        - RAG・ベクトル検索技術: RAG, FAISS, Chroma, Weaviate等
        - AIエージェントフレームワーク: AutoGPT, CrewAI, LangGraph等
        - マルチモーダルAI技術: Sora, DALL-E, Stable Diffusion等
        - AI評価・ベンチマーク: MMLU, HumanEval, SWE-bench等
        - AI研究機関・主要AI企業: Anthropic, OpenAI, Mistral AI, DeepMind等
        - AIコーディングツール（IDE統合型）: Cursor, Copilot, Cline, Continue等

        ## 除外対象（絶対に含めない）
        - AIを「使った」アプリ・サービス（料理アプリ、ライティングツール、チャットボット等）
        - 汎用プログラミング言語（Python, JavaScript, Rust等）
        - 汎用インフラ（Docker, Kubernetes, AWS, GCP等）
        - 古典的MLアルゴリズム（CNN, RNN, LSTM等）
        - 意味不明な略語・造語
        - 特定企業の内部ツール名（AI技術そのものでないもの）
        - 既知用語: {known_sample}

        ## 判断基準
        「この用語はAI技術者が技術文書で使う専門用語か？」→YES なら抽出
        「これはAIを使ったサービス/アプリの名前か？」→YES なら除外

        ## 出力形式（JSONのみ、説明文不要）
        {{"terms": [
          {{"term": "用語名", "theme": "テーマキー", "category": "カテゴリ"}}
        ]}}
        テーマキー: {theme_list}
        カテゴリ: {category_list}

        ## テキスト
        {texts_block}
    """).strip()


def _call_llm(prompt: str, max_tokens: int = 1024) -> str:
    """LLMを呼び出してテキストを返す（Groq/OpenAI共通インターフェース）。"""
    if client is None:
        return '{"terms":[]}'
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or '{"terms":[]}'


def _extract_terms_from_texts(texts: list[str], known_terms: set[str]) -> list[dict]:
    if client is None:
        return []
    results = []
    for i in range(0, len(texts), CHUNK_SIZE):
        chunk = texts[i:i + CHUNK_SIZE]
        prompt = _build_extraction_prompt(chunk, known_terms)
        try:
            content = _call_llm(prompt, max_tokens=1024)
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
    if client is None:
        return ""
    try:
        content = _call_llm(
            f"AI技術用語「{term}」について、日本語で100〜200文字の簡潔な説明文を書いてください。説明文のみ返してください。",
            max_tokens=400,
        )
        return content.strip()
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


def _filter_unknown_texts(texts: list[str], known_terms: set[str]) -> list[str]:
    """
    既知用語名を含むテキストを除外し、未知テキストのみを返す。

    TPD上限対策: 既知用語が登場するテキストはLLMに投げる必要がないため除外する。
    これにより処理量を大幅削減（約1/6〜1/8）できる。

    除外ロジック:
      テキスト中に既知用語名（大文字小文字無視）が単語として含まれている場合は除外。
      ただし、短い用語（3文字以下）は誤マッチを避けるため除外対象外とする。
    """
    # 誤マッチを避けるため4文字以上の既知用語のみ使用
    long_known = {t for t in known_terms if len(t) >= 4}

    unknown_texts = []
    skipped = 0
    for text in texts:
        text_lower = text.lower()
        # テキスト中に既知用語が含まれているかチェック
        if any(term in text_lower for term in long_known):
            skipped += 1
            continue
        unknown_texts.append(text)

    logger.info(
        f"[LLM] Text filtering: {len(texts)} total → {len(unknown_texts)} unknown "
        f"({skipped} skipped as already-known)"
    )
    return unknown_texts


def run_extraction() -> int:
    if client is None:
        logger.warning("[LLM] No LLM client available. Skipping extraction.")
        return 0

    today = date.today()
    known_terms = _get_known_terms()
    all_texts = _collect_texts_for_today()

    if not all_texts:
        logger.warning("[LLM] No texts to extract from. Skipping.")
        return 0

    # ── TPD上限対策: 既知用語を含むテキストを除外 ────────────────
    texts = _filter_unknown_texts(all_texts, known_terms)

    if not texts:
        logger.info("[LLM] All texts already covered by known terms. Skipping LLM call.")
        return 0

    logger.info(f"[LLM] Extracting terms from {len(texts)} texts using {LLM_PROVIDER}/{LLM_MODEL}...")
    extracted = _extract_terms_from_texts(texts, known_terms)

    # ── ノイズフィルタ ────────────────────────────────────────
    NOISE_TERMS = {
        "aws", "gcp", "azure", "docker", "kubernetes", "k8s",
        "python", "javascript", "typescript", "java", "go", "rust", "c",
        "linux", "windows", "macos", "ios", "android",
        "git", "github", "gitlab", "npm", "pip",
        "sql", "mysql", "postgres", "mongodb", "redis",
        "html", "css", "json", "xml", "yaml",
    }

    seen: set[str] = set(known_terms)
    new_terms = []
    for item in extracted:
        term_name = (item.get("term") or "").strip()
        if not term_name:
            continue
        if len(term_name) <= 1:
            continue
        if term_name.lower() in seen or term_name.lower() in NOISE_TERMS:
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
