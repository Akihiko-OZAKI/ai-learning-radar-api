"""
ニュースインデクサー

バッチ実行時に HN API から直接記事を取得し、
各用語に関連する記事を app.db の term_news テーブルに保存する。

マッチング方式: 用語名を含む HN タイトルを部分一致で検索（大文字小文字無視）
保持期間: 過去 90 日分のみ保持（古いものは削除）

設計方針:
  raw.db（raw_hn テーブル）には依存しない。
  GitHub Actions では raw.db が毎回空から始まるため、
  HN API を直接呼び出してインデクシングを行う。
"""

import logging
import time
from datetime import date, timedelta
from typing import Optional

import requests

from db import get_connection

logger = logging.getLogger(__name__)

KEEP_DAYS = 90  # term_news の保持日数
HN_BASE = "https://hacker-news.firebaseio.com/v0"

# 取得するストーリー種別と件数
STORY_ENDPOINTS = {
    "topstories": 500,
    "beststories": 200,
}

# AI 関連フィルタキーワード（タイトルに含まれる場合に収集対象とする）
AI_KEYWORDS = [
    "ai", "llm", "gpt", "claude", "gemini", "mistral", "llama",
    "openai", "anthropic", "deepseek", "grok",
    "machine learning", "deep learning", "neural",
    "langchain", "langgraph", "agent", "rag",
    "mcp", "model context", "copilot", "cursor",
    "transformer", "diffusion", "embedding",
    "inference", "fine-tun", "prompt",
]


def _is_ai_related(title: str) -> bool:
    """タイトルが AI 関連かどうかを判定する（大文字小文字を無視）。"""
    title_lower = title.lower()
    return any(kw in title_lower for kw in AI_KEYWORDS)


def _fetch_item(item_id: int) -> Optional[dict]:
    """HN アイテムを取得する。"""
    try:
        resp = requests.get(f"{HN_BASE}/item/{item_id}.json", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.debug(f"[News] Failed to fetch HN item {item_id}: {e}")
        return None


def _collect_hn_articles() -> list[dict]:
    """
    HN API から AI 関連記事を直接収集して返す。
    raw.db には依存しない。
    """
    today = str(date.today())
    seen_ids: set[int] = set()
    articles = []

    for endpoint, limit in STORY_ENDPOINTS.items():
        logger.info(f"[News] Fetching HN {endpoint} (limit={limit})")
        try:
            resp = requests.get(f"{HN_BASE}/{endpoint}.json", timeout=15)
            resp.raise_for_status()
            story_ids: list[int] = resp.json()[:limit]
        except requests.RequestException as e:
            logger.error(f"[News] Failed to fetch HN {endpoint}: {e}")
            continue

        for story_id in story_ids:
            if story_id in seen_ids:
                continue
            seen_ids.add(story_id)

            item = _fetch_item(story_id)
            if not item:
                continue
            if item.get("type") != "story":
                continue

            title = item.get("title") or ""
            if not _is_ai_related(title):
                continue

            articles.append({
                "hn_id": story_id,
                "title": title,
                "score": item.get("score", 0),
                "comments": item.get("descendants", 0),
                "collected_at": today,
            })
            time.sleep(0.05)  # API 負荷軽減

    logger.info(f"[News] Collected {len(articles)} AI-related HN articles")
    return articles


def run_news_indexing() -> int:
    """
    HN API から直接記事を取得して terms と照合し、term_news に保存する。

    Returns:
        新規保存した記事数
    """
    today = str(date.today())

    # 1. app.db から全用語を取得
    app_conn = get_connection()
    terms = app_conn.execute(
        "SELECT term_id, term_name FROM terms"
    ).fetchall()

    # 2. HN API から直接記事を収集（raw.db 非依存）
    hn_articles = _collect_hn_articles()

    if not hn_articles:
        logger.info("[News] No HN articles collected. Skipping indexing.")
        app_conn.close()
        return 0

    logger.info(f"[News] Indexing {len(hn_articles)} HN articles against {len(terms)} terms...")

    # 3. 用語ごとに関連記事を検索して保存
    saved = 0
    with app_conn:
        for term in terms:
            term_id = term["term_id"]
            term_name_lower = term["term_name"].lower()

            for article in hn_articles:
                if term_name_lower in article["title"].lower():
                    try:
                        app_conn.execute(
                            """INSERT OR IGNORE INTO term_news
                               (term_id, hn_id, title, score, comments, collected_at)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                term_id,
                                article["hn_id"],
                                article["title"],
                                article["score"],
                                article["comments"],
                                article["collected_at"],
                            ),
                        )
                        saved += 1
                    except Exception as e:
                        logger.debug(f"[News] Insert error: {e}")

        # 4. 古いニュースを削除（KEEP_DAYS 日より古いもの）
        cutoff = str(date.today() - timedelta(days=KEEP_DAYS))
        deleted = app_conn.execute(
            "DELETE FROM term_news WHERE collected_at < ?", (cutoff,)
        ).rowcount

    app_conn.close()
    logger.info(f"[News] Saved {saved} news items. Deleted {deleted} old items.")
    return saved
