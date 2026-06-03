"""
GitHub データ収集モジュール

GitHub Search API を使い、AI 関連リポジトリのメタデータを取得して
raw_github テーブルに保存する。

取得対象トピック: ai, llm, machine-learning, deep-learning,
                  langchain, agent, rag, mcp, generative-ai
"""

import json
import time
import logging
from datetime import date, timedelta
from typing import Optional

import requests

from db import get_raw_connection as get_connection

logger = logging.getLogger(__name__)

# GitHub Search API エンドポイント
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"

# 収集対象トピック（OR 検索）
AI_TOPICS = [
    "llm",
    "large-language-model",
    "generative-ai",
    "ai-agent",
    "langchain",
    "rag",
    "mcp",
    "machine-learning",
    "deep-learning",
    "chatgpt",
    "claude",
    "gemini",
]

# 1クエリあたりの最大取得件数（GitHub API 上限 100）
PER_PAGE = 100
# トピックごとの取得ページ数
MAX_PAGES = 3


def _get_headers(token: Optional[str] = None) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_repos_by_topic(topic: str, headers: dict) -> list[dict]:
    """指定トピックのリポジトリ一覧を取得する。"""
    repos = []
    for page in range(1, MAX_PAGES + 1):
        params = {
            "q": f"topic:{topic}",
            "sort": "stars",
            "order": "desc",
            "per_page": PER_PAGE,
            "page": page,
        }
        try:
            resp = requests.get(
                GITHUB_SEARCH_URL, headers=headers, params=params, timeout=30
            )
            if resp.status_code == 403:
                logger.warning("[GitHub] Rate limit hit. Sleeping 60s...")
                time.sleep(60)
                continue
            resp.raise_for_status()
            items = resp.json().get("items", [])
            repos.extend(items)
            if len(items) < PER_PAGE:
                break
            time.sleep(1)  # API レート制限対策
        except requests.RequestException as e:
            logger.error(f"[GitHub] Request error for topic={topic}: {e}")
            break
    return repos


def _calc_stars_delta(repo_name: str, stars: int, today: date) -> int:
    """前日のStar数との差分を計算する。"""
    yesterday = today - timedelta(days=1)
    conn = get_connection()
    row = conn.execute(
        "SELECT stars FROM raw_github WHERE repo_name=? AND collected_at=?",
        (repo_name, str(yesterday)),
    ).fetchone()
    conn.close()
    if row:
        return stars - row["stars"]
    return 0  # 初回収集時は差分なし


def collect(github_token: Optional[str] = None) -> int:
    """
    AI 関連リポジトリを収集し raw_github テーブルに保存する。

    Returns:
        保存した件数
    """
    today = date.today()
    headers = _get_headers(github_token)
    seen_repos: set[str] = set()
    rows_to_insert = []

    for topic in AI_TOPICS:
        logger.info(f"[GitHub] Fetching topic: {topic}")
        repos = _fetch_repos_by_topic(topic, headers)
        for repo in repos:
            repo_name = repo.get("full_name", "")
            if repo_name in seen_repos:
                continue
            seen_repos.add(repo_name)

            stars = repo.get("stargazers_count", 0)
            forks = repo.get("forks_count", 0)
            description = repo.get("description") or ""
            topics = json.dumps(repo.get("topics", []))
            stars_delta = _calc_stars_delta(repo_name, stars, today)

            rows_to_insert.append(
                (str(today), repo_name, description, topics, stars, forks, stars_delta)
            )

    conn = get_connection()
    with conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO raw_github
                (collected_at, repo_name, description, topics, stars, forks, stars_delta)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
    conn.close()
    logger.info(f"[GitHub] Saved {len(rows_to_insert)} repos for {today}")
    return len(rows_to_insert)
