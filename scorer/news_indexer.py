"""
ニュースインデクサー

バッチ実行時に raw_hn の記事を走査し、
各用語に関連する記事を app.db の term_news テーブルに保存する。

マッチング方式: 用語名を含む HN タイトルを部分一致で検索（大文字小文字無視）
保持期間: 過去 90 日分のみ保持（古いものは削除）
"""

import logging
from datetime import date, timedelta

from db import get_connection, get_raw_connection

logger = logging.getLogger(__name__)

KEEP_DAYS = 90  # term_news の保持日数


def run_news_indexing() -> int:
    """
    本日収集した raw_hn の記事を terms と照合して term_news に保存する。

    Returns:
        新規保存した記事数
    """
    today = str(date.today())

    # 1. app.db から全用語を取得
    app_conn = get_connection()
    terms = app_conn.execute(
        "SELECT term_id, term_name FROM terms"
    ).fetchall()

    # 2. raw.db から本日の HN 記事を取得
    raw_conn = get_raw_connection()
    hn_rows = raw_conn.execute(
        "SELECT hn_id, title, score, comments FROM raw_hn WHERE collected_at=?",
        (today,),
    ).fetchall()
    raw_conn.close()

    if not hn_rows:
        logger.info("[News] No HN articles for today. Skipping.")
        app_conn.close()
        return 0

    logger.info(f"[News] Indexing {len(hn_rows)} HN articles against {len(terms)} terms...")

    # 3. 用語ごとに関連記事を検索して保存
    saved = 0
    with app_conn:
        for term in terms:
            term_id = term["term_id"]
            term_name_lower = term["term_name"].lower()

            for row in hn_rows:
                if term_name_lower in row["title"].lower():
                    try:
                        app_conn.execute(
                            """INSERT OR IGNORE INTO term_news
                               (term_id, hn_id, title, score, comments, collected_at)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (term_id, row["hn_id"], row["title"],
                             row["score"], row["comments"], today),
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
