"""
LLMテーマ細分化マイグレーション

「llm」テーマを以下の3つに分割する:
  llm_model   : LLM Model   - モデル本体（重み・アーキテクチャ）
  llm_product : LLM Product - LLMを使ったプロダクト・UI・サービス
  llm_api     : LLM API/Dev - API・開発者向けサービス・SDK

分類ルール:
  - category = 'Model' → llm_model
  - 以下のキーワードを含む → llm_product
      ChatGPT, Claude.ai, Gemini App, Perplexity, Copilot, You.com,
      Pi, Character, Poe, Jasper, Notion AI, Bing Chat, Bard
  - 以下のキーワードを含む → llm_api
      API, SDK, Groq, Together, Fireworks, Replicate, Bedrock,
      VertexAI, OpenRouter, Hugging Face
  - それ以外 → llm_model（デフォルト）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db import get_connection

# LLM Product に分類するキーワード（term_name に含まれる場合）
PRODUCT_KEYWORDS = {
    "chatgpt", "claude.ai", "gemini app", "perplexity", "copilot",
    "you.com", "pi ", "character.ai", "poe", "jasper", "notion ai",
    "bing chat", "bard", "grok", "meta ai", "mistral chat",
    "deepseek chat", "qwen chat", "kimi", "doubao",
}

# LLM API/Dev に分類するキーワード
API_KEYWORDS = {
    " api", "sdk", "groq", "together ai", "fireworks", "replicate",
    "bedrock", "vertex", "openrouter", "hugging face", "huggingface",
    "inference", "endpoint", "gateway",
}


def classify_llm_term(term_name: str, category: str) -> str:
    """用語名とカテゴリからLLMサブテーマを決定する。"""
    name_lower = term_name.lower()

    # APIキーワードチェック
    for kw in API_KEYWORDS:
        if kw in name_lower:
            return "llm_api"

    # Productキーワードチェック
    for kw in PRODUCT_KEYWORDS:
        if kw in name_lower:
            return "llm_product"

    # カテゴリベース
    if category in ("Model", "Other", "Framework", "Library"):
        return "llm_model"
    if category == "Tool":
        return "llm_product"

    return "llm_model"


def migrate():
    conn = get_connection()

    # 1. 新テーマを追加
    new_themes = [
        ("llm_model",   "LLM Model"),
        ("llm_product", "LLM Product"),
        ("llm_api",     "LLM API/Dev"),
    ]
    for key, name in new_themes:
        conn.execute(
            "INSERT OR IGNORE INTO themes (theme_key, theme_name) VALUES (?, ?)",
            (key, name),
        )
    conn.commit()

    # 2. 新テーマのIDを取得
    theme_ids = {}
    for key, _ in new_themes:
        row = conn.execute(
            "SELECT theme_id FROM themes WHERE theme_key=?", (key,)
        ).fetchone()
        theme_ids[key] = row["theme_id"]

    # 3. 旧 llm テーマのIDを取得
    old_llm_id = conn.execute(
        "SELECT theme_id FROM themes WHERE theme_key='llm'"
    ).fetchone()["theme_id"]

    # 4. 旧 llm 用語を新テーマに再分類
    terms = conn.execute(
        "SELECT term_id, term_name, category FROM terms WHERE theme_id=?",
        (old_llm_id,),
    ).fetchall()

    counts = {"llm_model": 0, "llm_product": 0, "llm_api": 0}
    for row in terms:
        new_theme_key = classify_llm_term(row["term_name"], row["category"])
        new_theme_id = theme_ids[new_theme_key]
        conn.execute(
            "UPDATE terms SET theme_id=? WHERE term_id=?",
            (new_theme_id, row["term_id"]),
        )
        counts[new_theme_key] += 1

    conn.commit()

    # 5. 旧 llm テーマを削除（用語が0件になった場合のみ）
    remaining = conn.execute(
        "SELECT COUNT(*) FROM terms WHERE theme_id=?", (old_llm_id,)
    ).fetchone()[0]
    if remaining == 0:
        conn.execute("DELETE FROM themes WHERE theme_key='llm'")
        conn.commit()
        print("旧 llm テーマを削除しました")
    else:
        print(f"旧 llm テーマに {remaining} 件残っています（手動確認推奨）")

    conn.close()

    print("\n=== マイグレーション完了 ===")
    for key, cnt in counts.items():
        print(f"  {key}: {cnt}件")
    print(f"  合計: {sum(counts.values())}件")


if __name__ == "__main__":
    migrate()
