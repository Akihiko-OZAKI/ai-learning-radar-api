"""
AI変動指数（AI Volatility Index）計算モジュール

仕様書§8に基づく計算式:
  40% : 急上昇率     - 前日比でスコアが大きく上昇した用語の割合
  30% : ランキング入替率 - 前日からランキング順位が変動した用語の割合
  20% : 新規用語出現率  - 過去7日以内に初登場した用語の割合
  10% : 総言及量変化   - 全用語の総スコア合計の前日比変化率

結果は 0〜100 のスコアとして返す。
計算式の係数は定数として定義し、将来変更可能にする。
"""

import logging
from datetime import date, timedelta
from typing import Optional

from db import get_connection

logger = logging.getLogger(__name__)

# ── 計算式の係数（将来変更可能） ─────────────────────────────
WEIGHT_SURGE_RATE    = 0.40  # 急上昇率の重み
WEIGHT_RANK_CHANGE   = 0.30  # ランキング入替率の重み
WEIGHT_NEW_TERM_RATE = 0.20  # 新規用語出現率の重み
WEIGHT_VOLUME_CHANGE = 0.10  # 総言及量変化の重み

# 急上昇の閾値: 前日比スコアがこの倍率以上を「急上昇」とみなす
SURGE_THRESHOLD = 1.5  # 50%以上の増加

# 新規用語の定義: 過去N日以内に初登場した用語
NEW_TERM_DAYS = 7

# 総言及量変化の正規化上限（この変化率を100%とみなす）
VOLUME_CHANGE_MAX = 0.5  # 50%変化で最大寄与

# ── ラベル定義 ─────────────────────────────────────────────
VOLATILITY_LABELS = [
    (80, 100, "パラダイムシフト"),
    (50,  80, "急変"),
    (20,  50, "活発"),
    (0,   20, "安定"),
]


def _get_label(score: float) -> str:
    for low, high, label in VOLATILITY_LABELS:
        if low <= score <= high:
            return label
    return "安定"


def _calc_surge_rate(today: str, yesterday: str) -> float:
    """
    急上昇率: 前日比でスコアが SURGE_THRESHOLD 倍以上になった用語の割合。
    前日データがない場合は 0 を返す。
    """
    conn = get_connection()
    today_scores = conn.execute(
        "SELECT term_id, total_score FROM daily_scores WHERE date=?", (today,)
    ).fetchall()
    if not today_scores:
        conn.close()
        return 0.0

    surge_count = 0
    total_count = len(today_scores)

    for row in today_scores:
        term_id = row["term_id"]
        today_score = row["total_score"]
        prev = conn.execute(
            "SELECT total_score FROM daily_scores WHERE term_id=? AND date=?",
            (term_id, yesterday),
        ).fetchone()
        if prev and prev["total_score"] > 0:
            ratio = today_score / prev["total_score"]
            if ratio >= SURGE_THRESHOLD:
                surge_count += 1
        elif prev is None and today_score > 0:
            # 前日データなし（新規）も急上昇とみなす
            surge_count += 1

    conn.close()
    return surge_count / total_count if total_count > 0 else 0.0


def _calc_rank_change_rate(today: str, yesterday: str) -> float:
    """
    ランキング入替率: 前日から順位が変動した用語の割合。
    """
    conn = get_connection()
    today_ranks = {
        row["term_id"]: row["rank"]
        for row in conn.execute(
            "SELECT term_id, rank FROM daily_scores WHERE date=? AND rank IS NOT NULL",
            (today,),
        ).fetchall()
    }
    yesterday_ranks = {
        row["term_id"]: row["rank"]
        for row in conn.execute(
            "SELECT term_id, rank FROM daily_scores WHERE date=? AND rank IS NOT NULL",
            (yesterday,),
        ).fetchall()
    }
    conn.close()

    if not today_ranks or not yesterday_ranks:
        return 0.0

    # 両日に存在する用語のみ比較
    common = set(today_ranks.keys()) & set(yesterday_ranks.keys())
    if not common:
        return 0.0

    changed = sum(1 for tid in common if today_ranks[tid] != yesterday_ranks[tid])
    return changed / len(common)


def _calc_new_term_rate(today: str) -> float:
    """
    新規用語出現率: 過去 NEW_TERM_DAYS 日以内に初登場した用語の割合。
    """
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM terms").fetchone()[0]
    if total == 0:
        conn.close()
        return 0.0

    cutoff = str(date.fromisoformat(today) - timedelta(days=NEW_TERM_DAYS))
    new_count = conn.execute(
        "SELECT COUNT(*) FROM terms WHERE first_seen >= ?", (cutoff,)
    ).fetchone()[0]
    conn.close()
    return new_count / total


def _calc_volume_change(today: str, yesterday: str) -> float:
    """
    総言及量変化: 全用語の総スコア合計の前日比変化率（正規化済み）。
    """
    conn = get_connection()
    today_total = conn.execute(
        "SELECT COALESCE(SUM(total_score), 0) FROM daily_scores WHERE date=?", (today,)
    ).fetchone()[0]
    yesterday_total = conn.execute(
        "SELECT COALESCE(SUM(total_score), 0) FROM daily_scores WHERE date=?", (yesterday,)
    ).fetchone()[0]
    conn.close()

    if yesterday_total == 0:
        return 0.0

    change_rate = abs(today_total - yesterday_total) / yesterday_total
    # VOLUME_CHANGE_MAX を上限として 0〜1 に正規化
    return min(change_rate / VOLUME_CHANGE_MAX, 1.0)


def calc_volatility_index(target_date: Optional[str] = None) -> dict:
    """
    AI変動指数を計算して返す。

    Returns:
        {
            "date": str,
            "score": float,          # 0〜100
            "label": str,            # 安定/活発/急変/パラダイムシフト
            "components": {
                "surge_rate": float,       # 急上昇率（0〜1）
                "rank_change_rate": float, # ランキング入替率（0〜1）
                "new_term_rate": float,    # 新規用語出現率（0〜1）
                "volume_change": float,    # 総言及量変化（0〜1）
            },
            "weights": {
                "surge_rate": float,
                "rank_change_rate": float,
                "new_term_rate": float,
                "volume_change": float,
            }
        }
    """
    today = target_date or str(date.today())
    yesterday = str(date.fromisoformat(today) - timedelta(days=1))

    surge_rate    = _calc_surge_rate(today, yesterday)
    rank_change   = _calc_rank_change_rate(today, yesterday)
    new_term_rate = _calc_new_term_rate(today)
    volume_change = _calc_volume_change(today, yesterday)

    # 加重平均で 0〜100 に変換
    raw_score = (
        surge_rate    * WEIGHT_SURGE_RATE +
        rank_change   * WEIGHT_RANK_CHANGE +
        new_term_rate * WEIGHT_NEW_TERM_RATE +
        volume_change * WEIGHT_VOLUME_CHANGE
    )
    score = round(min(raw_score * 100, 100), 1)

    result = {
        "date": today,
        "score": score,
        "label": _get_label(score),
        "components": {
            "surge_rate":       round(surge_rate, 4),
            "rank_change_rate": round(rank_change, 4),
            "new_term_rate":    round(new_term_rate, 4),
            "volume_change":    round(volume_change, 4),
        },
        "weights": {
            "surge_rate":       WEIGHT_SURGE_RATE,
            "rank_change_rate": WEIGHT_RANK_CHANGE,
            "new_term_rate":    WEIGHT_NEW_TERM_RATE,
            "volume_change":    WEIGHT_VOLUME_CHANGE,
        },
    }

    logger.info(
        f"[Volatility] {today}: score={score} ({result['label']}) "
        f"surge={surge_rate:.3f} rank={rank_change:.3f} "
        f"new={new_term_rate:.3f} vol={volume_change:.3f}"
    )
    return result
