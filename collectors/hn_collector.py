"""
Hacker News データ収集モジュール

HN Firebase API を使い、Top Stories / Best Stories から
AI 関連記事を取得して raw_hn テーブルに保存する。

フィルタリング: タイトルに AI 関連キーワードを含む記事のみ保存する。
"""

import time
import logging
from datetime import date, datetime
from typing import Optional

import requests

from db import get_raw_connection as get_connection

logger = logging.getLogger(__name__)

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
        resp = requests.get(
            f"{HN_BASE}/item/{item_id}.json", timeout=10
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.debug(f"[HN] Failed to fetch item {item_id}: {e}")
        return None


def collect() -> int:
    """
    HN Top/Best Stories から AI 関連記事を収集し raw_hn テーブルに保存する。

    Returns:
        保存した件数
    """
    today = date.today()
    seen_ids: set[int] = set()
    rows_to_insert = []

    for endpoint, limit in STORY_ENDPOINTS.items():
        logger.info(f"[HN] Fetching {endpoint} (limit={limit})")
        try:
            resp = requests.get(f"{HN_BASE}/{endpoint}.json", timeout=15)
            resp.raise_for_status()
            story_ids: list[int] = resp.json()[:limit]
        except requests.RequestException as e:
            logger.error(f"[HN] Failed to fetch {endpoint}: {e}")
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

            score = item.get("score", 0)
            comments = item.get("descendants", 0)
            ts = item.get("time")
            timestamp = (
                datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                if ts else None
            )

            rows_to_insert.append(
                (str(today), story_id, title, score, comments, timestamp)
            )
            time.sleep(0.05)  # API 負荷軽減

    conn = get_connection()
    with conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO raw_hn
                (collected_at, hn_id, title, score, comments, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
    conn.close()
    logger.info(f"[HN] Saved {len(rows_to_insert)} articles for {today}")
    return len(rows_to_insert)
