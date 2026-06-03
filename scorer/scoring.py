"""
決定論的スコアリング・上昇理由判定・ランキング生成モジュール

LLM を一切使用しない。同一入力から同一結果を保証する。

スコア計算式（PoC v0.1）:
  github_score = (stars_delta * 1.0 + stars * 0.001 + forks * 0.1) の用語別合計
  hn_score     = (score * 1.0 + comments * 0.5 + post_count * 10.0) の用語別合計
  total_score  = github_score * 0.7 + hn_score * 0.3

上昇理由判定:
  前日比スコアが上昇した用語に対し、各指標の寄与量を比較して最大のものをラベルとする。
"""

import json
import logging
from datetime import date, timedelta
from typing import Optional

from db import get_connection, get_raw_connection

logger = logging.getLogger(__name__)

# スコア重み（将来変更可能）
GITHUB_WEIGHT = 0.7
HN_WEIGHT = 0.3

# GitHub スコア係数
GH_STARS_DELTA_COEF = 1.0
GH_STARS_COEF = 0.001
GH_FORKS_COEF = 0.1

# HN スコア係数
HN_SCORE_COEF = 1.0
HN_COMMENTS_COEF = 0.5
HN_POST_COUNT_COEF = 10.0

# Top N 以内で is_permanent = True にする
PERMANENT_TOP_N = 20


def _get_terms() -> list[dict]:
    """全用語を返す。"""
    conn = get_connection()
    rows = conn.execute("SELECT term_id, term_name FROM terms").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _calc_github_score_for_term(term_name: str, today: str) -> tuple[float, dict]:
    """
    指定用語の GitHub スコアと各指標の寄与量を計算する。

    Returns:
        (github_score, contributions)
        contributions = {"stars_delta": float, "stars": float, "forks": float}
    """
    conn = get_raw_connection()
    rows = conn.execute(
        """
        SELECT stars, forks, stars_delta, description, topics, repo_name
        FROM raw_github
        WHERE collected_at = ?
        """,
        (today,),
    ).fetchall()
    conn.close()

    term_lower = term_name.lower()
    stars_delta_contrib = 0.0
    stars_contrib = 0.0
    forks_contrib = 0.0

    for row in rows:
        # テキストに用語が含まれるか判定
        searchable = " ".join(filter(None, [
            row["repo_name"] or "",
            row["description"] or "",
            " ".join(json.loads(row["topics"] or "[]")),
        ])).lower()

        if term_lower not in searchable:
            continue

        stars_delta_contrib += row["stars_delta"] * GH_STARS_DELTA_COEF
        stars_contrib += row["stars"] * GH_STARS_COEF
        forks_contrib += row["forks"] * GH_FORKS_COEF

    github_score = stars_delta_contrib + stars_contrib + forks_contrib
    contributions = {
        "stars_delta": stars_delta_contrib,
        "stars": stars_contrib,
        "forks": forks_contrib,
    }
    return github_score, contributions


def _calc_hn_score_for_term(term_name: str, today: str) -> tuple[float, dict]:
    """
    指定用語の HN スコアと各指標の寄与量を計算する。

    Returns:
        (hn_score, contributions)
        contributions = {"score": float, "comments": float, "post_count": float}
    """
    conn = get_raw_connection()
    rows = conn.execute(
        """
        SELECT title, score, comments
        FROM raw_hn
        WHERE collected_at = ?
        """,
        (today,),
    ).fetchall()
    conn.close()

    term_lower = term_name.lower()
    score_contrib = 0.0
    comments_contrib = 0.0
    post_count_contrib = 0.0

    for row in rows:
        title_lower = (row["title"] or "").lower()
        if term_lower not in title_lower:
            continue

        score_contrib += row["score"] * HN_SCORE_COEF
        comments_contrib += row["comments"] * HN_COMMENTS_COEF
        post_count_contrib += HN_POST_COUNT_COEF  # 1件あたり固定加算

    hn_score = score_contrib + comments_contrib + post_count_contrib
    contributions = {
        "score": score_contrib,
        "comments": comments_contrib,
        "post_count": post_count_contrib,
    }
    return hn_score, contributions


def _determine_rise_reason(
    total_score: float,
    prev_total_score: float,
    gh_contrib: dict,
    hn_contrib: dict,
) -> Optional[str]:
    """
    前日比スコアが上昇した場合、最も寄与した指標を上昇理由として返す。
    下降・横ばいの場合は None を返す。
    """
    if total_score <= prev_total_score:
        return None

    candidates = {
        "GitHub Star増加": gh_contrib.get("stars_delta", 0),
        "GitHub Fork増加": gh_contrib.get("forks", 0),
        "HN投稿増加":      hn_contrib.get("post_count", 0),
        "HNスコア増加":    hn_contrib.get("score", 0),
        "HNコメント増加":  hn_contrib.get("comments", 0),
    }

    # 全寄与量が0の場合は「複合上昇」
    total_contrib = sum(candidates.values())
    if total_contrib == 0:
        return "複合上昇"

    # 最大寄与の指標を返す
    best = max(candidates, key=lambda k: candidates[k])
    # 最大値が全体の 40% 未満なら「複合上昇」
    if candidates[best] / total_contrib < 0.4:
        return "複合上昇"

    return best


def _get_prev_score(term_id: int, yesterday: str) -> float:
    """前日の total_score を返す。なければ 0。"""
    conn = get_connection()
    row = conn.execute(
        "SELECT total_score FROM daily_scores WHERE term_id=? AND date=?",
        (term_id, yesterday),
    ).fetchone()
    conn.close()
    return row["total_score"] if row else 0.0


def _get_prev_rank(term_id: int, yesterday: str) -> Optional[int]:
    """前日の rank を返す。なければ None。"""
    conn = get_connection()
    row = conn.execute(
        "SELECT rank FROM daily_scores WHERE term_id=? AND date=?",
        (term_id, yesterday),
    ).fetchone()
    conn.close()
    return row["rank"] if row else None


def run_scoring() -> int:
    """
    本日の収集データに基づき全用語のスコアを計算し、
    daily_scores テーブルに保存してランキングを確定する。

    Returns:
        スコアを記録した用語数
    """
    today = str(date.today())
    yesterday = str(date.today() - timedelta(days=1))
    terms = _get_terms()

    if not terms:
        logger.warning("[Scorer] No terms in DB. Skipping scoring.")
        return 0

    logger.info(f"[Scorer] Scoring {len(terms)} terms for {today}...")

    scored = []
    for term in terms:
        term_id = term["term_id"]
        term_name = term["term_name"]

        github_score, gh_contrib = _calc_github_score_for_term(term_name, today)
        hn_score, hn_contrib = _calc_hn_score_for_term(term_name, today)
        total_score = github_score * GITHUB_WEIGHT + hn_score * HN_WEIGHT

        prev_total = _get_prev_score(term_id, yesterday)
        rise_reason = _determine_rise_reason(total_score, prev_total, gh_contrib, hn_contrib)

        scored.append({
            "term_id": term_id,
            "term_name": term_name,
            "github_score": github_score,
            "hn_score": hn_score,
            "total_score": total_score,
            "rise_reason": rise_reason,
        })

    # total_score 降順でランキングを付与
    scored.sort(key=lambda x: x["total_score"], reverse=True)

    conn = get_connection()
    with conn:
        for rank, item in enumerate(scored, start=1):
            term_id = item["term_id"]
            prev_rank = _get_prev_rank(term_id, yesterday)
            rank_change = (prev_rank - rank) if prev_rank is not None else None

            conn.execute(
                """
                INSERT OR REPLACE INTO daily_scores
                    (term_id, date, github_score, hn_score, total_score,
                     rank, rank_change, rise_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    term_id, today,
                    item["github_score"], item["hn_score"], item["total_score"],
                    rank, rank_change, item["rise_reason"],
                ),
            )

            # Top20 入りで is_permanent = True に更新
            if rank <= PERMANENT_TOP_N:
                conn.execute(
                    """
                    UPDATE terms
                    SET is_permanent = 1,
                        last_seen = ?,
                        peak_rank = CASE
                            WHEN peak_rank IS NULL OR ? < peak_rank THEN ?
                            ELSE peak_rank
                        END
                    WHERE term_id = ?
                    """,
                    (today, rank, rank, term_id),
                )

    conn.close()
    logger.info(f"[Scorer] Scoring complete. {len(scored)} terms scored.")
    return len(scored)
