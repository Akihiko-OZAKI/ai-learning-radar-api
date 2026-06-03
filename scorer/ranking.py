"""
ランキング取得モジュール

daily_scores と terms テーブルを結合し、
各種ランキングを dict のリストとして返す。
"""

import logging
from datetime import date
from typing import Optional

from db import get_connection

logger = logging.getLogger(__name__)


def get_popular_ranking(target_date: Optional[str] = None, limit: int = 20) -> list[dict]:
    """
    人気ランキング（total_score 降順）を返す。

    Returns:
        [{"rank", "term_name", "theme_name", "total_score",
          "rank_change", "rise_reason"}, ...]
    """
    d = target_date or str(date.today())
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            ds.rank,
            t.term_name,
            th.theme_name,
            t.category,
            ds.total_score,
            ds.rank_change,
            ds.rise_reason
        FROM daily_scores ds
        JOIN terms t ON ds.term_id = t.term_id
        LEFT JOIN themes th ON t.theme_id = th.theme_id
        WHERE ds.date = ?
        ORDER BY ds.rank ASC
        LIMIT ?
        """,
        (d, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_rising_ranking(target_date: Optional[str] = None, limit: int = 20) -> list[dict]:
    """
    急上昇ランキング（rank_change 降順）を返す。
    rank_change が NULL（初回）の用語は除外する。

    Returns:
        [{"rank", "term_name", "theme_name", "total_score",
          "rank_change", "rise_reason"}, ...]
    """
    d = target_date or str(date.today())
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            ds.rank,
            t.term_name,
            th.theme_name,
            t.category,
            ds.total_score,
            ds.rank_change,
            ds.rise_reason
        FROM daily_scores ds
        JOIN terms t ON ds.term_id = t.term_id
        LEFT JOIN themes th ON t.theme_id = th.theme_id
        WHERE ds.date = ?
          AND ds.rank_change IS NOT NULL
          AND ds.rank_change > 0
        ORDER BY ds.rank_change DESC
        LIMIT ?
        """,
        (d, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_new_terms(days: int = 30, limit: int = 10) -> list[dict]:
    """
    過去 N 日以内に初登場した用語（新規発見）を返す。

    Returns:
        [{"term_name", "theme_name", "category", "first_seen", "total_score"}, ...]
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            t.term_name,
            th.theme_name,
            t.category,
            t.first_seen,
            ds.total_score
        FROM terms t
        LEFT JOIN themes th ON t.theme_id = th.theme_id
        LEFT JOIN daily_scores ds
            ON t.term_id = ds.term_id AND ds.date = ?
        WHERE t.first_seen >= date('now', ?)
        ORDER BY ds.total_score DESC
        LIMIT ?
        """,
        (str(date.today()), f"-{days} days", limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_theme_ranking(target_date: Optional[str] = None, limit: int = 9) -> list[dict]:
    """
    テーマランキング（テーマ別 total_score 合計の降順）を返す。

    Returns:
        [{"theme_name", "total_score", "term_count"}, ...]
    """
    d = target_date or str(date.today())
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            th.theme_name,
            SUM(ds.total_score) AS total_score,
            COUNT(ds.term_id)   AS term_count
        FROM daily_scores ds
        JOIN terms t ON ds.term_id = t.term_id
        JOIN themes th ON t.theme_id = th.theme_id
        WHERE ds.date = ?
        GROUP BY th.theme_id
        ORDER BY total_score DESC
        LIMIT ?
        """,
        (d, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def print_rankings(target_date: Optional[str] = None) -> None:
    """ランキングをコンソールに整形出力する（動作確認用）。"""
    d = target_date or str(date.today())
    print(f"\n{'='*60}")
    print(f"  AI変化観測所  ランキング  [{d}]")
    print(f"{'='*60}")

    # テーマランキング
    print("\n[AIテーマランキング]")
    print(f"{'順位':<4} {'テーマ':<22} {'スコア':>10} {'用語数':>6}")
    print("-" * 50)
    for i, r in enumerate(get_theme_ranking(d), 1):
        print(f"{i:<4} {r['theme_name']:<22} {r['total_score']:>10.1f} {r['term_count']:>6}")

    # 人気ランキング
    print("\n[人気ランキング TOP20]")
    print(f"{'順位':<4} {'用語':<20} {'テーマ':<18} {'スコア':>8} {'変動':>6} {'上昇理由'}")
    print("-" * 80)
    for r in get_popular_ranking(d):
        change_str = (
            f"+{r['rank_change']}" if r["rank_change"] and r["rank_change"] > 0
            else str(r["rank_change"]) if r["rank_change"] is not None
            else "NEW"
        )
        reason = r["rise_reason"] or "-"
        print(
            f"{r['rank']:<4} {r['term_name']:<20} "
            f"{(r['theme_name'] or '-'):<18} "
            f"{r['total_score']:>8.1f} {change_str:>6}  {reason}"
        )

    # 急上昇ランキング
    print("\n[急上昇ランキング TOP20]")
    print(f"{'順位':<4} {'用語':<20} {'テーマ':<18} {'上昇幅':>6} {'上昇理由'}")
    print("-" * 70)
    for i, r in enumerate(get_rising_ranking(d), 1):
        reason = r["rise_reason"] or "-"
        print(
            f"{i:<4} {r['term_name']:<20} "
            f"{(r['theme_name'] or '-'):<18} "
            f"+{r['rank_change']:>5}  {reason}"
        )

    # 新規発見
    print("\n[新規発見（過去30日）]")
    print(f"{'用語':<20} {'テーマ':<18} {'初観測日':<12} {'スコア':>8}")
    print("-" * 65)
    for r in get_new_terms():
        score_str = f"{r['total_score']:.1f}" if r["total_score"] is not None else "-"
        print(
            f"{r['term_name']:<20} {(r['theme_name'] or '-'):<18} "
            f"{r['first_seen']:<12} {score_str:>8}"
        )
    print()
