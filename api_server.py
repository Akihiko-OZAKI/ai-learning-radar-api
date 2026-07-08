"""
AI変化観測所 - FastAPI バックエンド API サーバー

Next.js フロントエンドから呼び出される REST API を提供する。
ポート: 8000

エンドポイント:
  GET /api/ranking/popular          人気ランキング
  GET /api/ranking/rising           急上昇ランキング
  GET /api/ranking/themes           テーマランキング
  GET /api/ranking/new              新規発見用語
  GET /api/term/{term_name}         用語詳細
  GET /api/term/{term_name}/history スコア推移
  GET /api/status                   最終更新日時・用語数
  GET /api/volatility                AI変動指数
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import date, timedelta
from typing import Optional

from db import get_connection
from scorer.volatility import calc_volatility_index

app = FastAPI(title="AI変化観測所 API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _today() -> str:
    return str(date.today())


def _latest_date() -> str:
    """daily_scoresに存在する最新の日付を返す。なければ今日を返す。"""
    conn = get_connection()
    row = conn.execute("SELECT MAX(date) as d FROM daily_scores").fetchone()
    conn.close()
    return row["d"] if row and row["d"] else _today()


@app.get("/api/status")
def get_status():
    """最終更新日時・登録用語数・is_permanent用語数を返す。"""
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) as c FROM terms").fetchone()["c"]
    permanent = conn.execute(
        "SELECT COUNT(*) as c FROM terms WHERE is_permanent=1"
    ).fetchone()["c"]
    last_date = conn.execute(
        "SELECT MAX(date) as d FROM daily_scores"
    ).fetchone()["d"]
    conn.close()
    return {
        "last_updated": last_date,
        "total_terms": total,
        "permanent_terms": permanent,
    }


@app.get("/api/ranking/popular")
def get_popular_ranking(
    date: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """人気ランキング（total_score 降順）。"""
    d = date or _latest_date()
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            ds.rank,
            t.term_name,
            th.theme_name,
            t.category,
            ROUND(ds.total_score, 1) AS total_score,
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
    return {"date": d, "items": [dict(r) for r in rows]}


@app.get("/api/ranking/rising")
def get_rising_ranking(
    date: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """急上昇ランキング（rank_change 降順）。"""
    d = date or _latest_date()
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            ds.rank,
            t.term_name,
            th.theme_name,
            t.category,
            ROUND(ds.total_score, 1) AS total_score,
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
    return {"date": d, "items": [dict(r) for r in rows]}


@app.get("/api/ranking/themes")
def get_theme_ranking(
    date: Optional[str] = Query(None),
    limit: int = Query(9, ge=1, le=20),
):
    """テーマランキング（テーマ別 total_score 合計の降順）。"""
    d = date or _latest_date()
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            th.theme_key,
            th.theme_name,
            ROUND(SUM(ds.total_score), 1) AS total_score,
            COUNT(ds.term_id) AS term_count
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
    return {"date": d, "items": [dict(r) for r in rows]}


@app.get("/api/ranking/new")
def get_new_terms(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
):
    """過去 N 日以内に初登場した用語。"""
    d = _today()
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            t.term_name,
            th.theme_name,
            t.category,
            t.first_seen,
            ROUND(ds.total_score, 1) AS total_score
        FROM terms t
        LEFT JOIN themes th ON t.theme_id = th.theme_id
        LEFT JOIN daily_scores ds
            ON t.term_id = ds.term_id AND ds.date = ?
        WHERE t.first_seen >= date('now', ?)
        ORDER BY ds.total_score DESC
        LIMIT ?
        """,
        (d, f"-{days} days", limit),
    ).fetchall()
    conn.close()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/term/{term_name}")
def get_term_detail(term_name: str):
    """用語詳細情報を返す。"""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT
            t.term_id,
            t.term_name,
            th.theme_key,
            th.theme_name,
            t.category,
            t.first_seen,
            t.last_seen,
            t.peak_rank,
            t.description,
            t.is_permanent
        FROM terms t
        LEFT JOIN themes th ON t.theme_id = th.theme_id
        WHERE LOWER(t.term_name) = LOWER(?)
        """,
        (term_name,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Term not found")

    term_id = row["term_id"]

    # 今日のスコア・順位
    today_score = conn.execute(
        """
        SELECT rank, rank_change, ROUND(total_score,1) as total_score,
               ROUND(github_score,1) as github_score,
               ROUND(hn_score,1) as hn_score,
               rise_reason
        FROM daily_scores WHERE term_id=? AND date=?
        """,
        (term_id, _today()),
    ).fetchone()

    conn.close()

    result = dict(row)
    result["today"] = dict(today_score) if today_score else None
    return result


@app.get("/api/term/{term_name}/history")
def get_term_history(
    term_name: str,
    days: int = Query(30, ge=7, le=365),
):
    """用語のスコア推移を返す。"""
    conn = get_connection()
    row = conn.execute(
        "SELECT term_id FROM terms WHERE LOWER(term_name) = LOWER(?)",
        (term_name,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Term not found")

    term_id = row["term_id"]
    rows = conn.execute(
        """
        SELECT
            date,
            ROUND(total_score, 1) AS total_score,
            ROUND(github_score, 1) AS github_score,
            ROUND(hn_score, 1) AS hn_score,
            rank
        FROM daily_scores
        WHERE term_id = ?
          AND date >= date('now', ?)
        ORDER BY date ASC
        """,
        (term_id, f"-{days} days"),
    ).fetchall()
    conn.close()
    return {"term_name": term_name, "days": days, "history": [dict(r) for r in rows]}


@app.get("/api/volatility")
def get_volatility(
    date: Optional[str] = Query(None),
):
    """
    AI変動指数を返す。

    Returns:
        {
            "date": str,
            "score": float,       # 0〜100
            "label": str,         # 安定/活発/急変/パラダイムシフト
            "components": {...},  # 各成分の内訳
            "weights": {...}      # 重み係数
        }
    """
    return calc_volatility_index(target_date=date)


@app.get("/api/term/{term_name}/news")
def get_term_news(
    term_name: str,
    days: int = Query(30, ge=1, le=90),
    limit: int = Query(10, ge=1, le=30),
):
    """
    用語に関連する HN 記事を返す。

    用語名を含む HN タイトルを過去 N 日分の raw_hn から検索する。
    スコア順にソートして返す。

    Returns:
        {
            "term_name": str,
            "items": [
                {
                    "title": str,
                    "score": int,
                    "comments": int,
                    "collected_at": str,
                    "hn_id": int
                }
            ]
        }
    """
    from db import get_raw_connection
    conn = get_raw_connection()
    rows = conn.execute(
        """
        SELECT title, score, comments, collected_at, hn_id
        FROM raw_hn
        WHERE LOWER(title) LIKE LOWER(?)
          AND collected_at >= date('now', ?)
        ORDER BY score DESC, comments DESC
        LIMIT ?
        """,
        (f"%{term_name}%", f"-{days} days", limit),
    ).fetchall()
    conn.close()
    return {"term_name": term_name, "items": [dict(r) for r in rows]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
