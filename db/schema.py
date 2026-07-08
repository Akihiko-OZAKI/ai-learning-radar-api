"""
DB スキーマ定義と初期化モジュール

DB構成:
  app.db  - アプリ用軽量DB（themes, terms, daily_scores）
            → Gitに含めてRenderにデプロイ
  raw.db  - 生データDB（raw_github, raw_hn）
            → .gitignoreで除外、ローカルのみ
"""

import sqlite3
import os
from pathlib import Path

DB_DIR   = Path(__file__).parent
APP_DB   = DB_DIR / "app.db"
RAW_DB   = DB_DIR / "raw.db"

# 後方互換: DB_PATH は app.db を指す
DB_PATH  = APP_DB

THEMES_SEED = [
    ("llm",              "LLM"),
    ("ai_coding",        "AI Coding"),
    ("ai_agent",         "AI Agent"),
    ("tool_integration", "Tool Integration"),
    ("retrieval",        "Retrieval"),
    ("ai_infra",         "AI Infra"),
    ("multimodal",       "Multimodal"),
    ("ai_framework",     "AI Framework"),
    ("other",            "Other"),
]

APP_DDL = """
CREATE TABLE IF NOT EXISTS themes (
    theme_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_key  TEXT    NOT NULL UNIQUE,
    theme_name TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS terms (
    term_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    term_name    TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    theme_id     INTEGER REFERENCES themes(theme_id),
    category     TEXT,
    first_seen   DATE    NOT NULL,
    last_seen    DATE    NOT NULL,
    peak_rank    INTEGER,
    description  TEXT,
    is_permanent INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_scores (
    score_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id      INTEGER NOT NULL REFERENCES terms(term_id),
    date         DATE    NOT NULL,
    github_score REAL    NOT NULL DEFAULT 0,
    hn_score     REAL    NOT NULL DEFAULT 0,
    total_score  REAL    NOT NULL DEFAULT 0,
    rank         INTEGER,
    rank_change  INTEGER,
    rise_reason  TEXT,
    UNIQUE(term_id, date)
);

CREATE TABLE IF NOT EXISTS term_news (
    news_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id      INTEGER NOT NULL REFERENCES terms(term_id),
    hn_id        INTEGER NOT NULL,
    title        TEXT    NOT NULL,
    score        INTEGER NOT NULL DEFAULT 0,
    comments     INTEGER NOT NULL DEFAULT 0,
    collected_at DATE    NOT NULL,
    UNIQUE(term_id, hn_id)
);

CREATE INDEX IF NOT EXISTS idx_term_news_term_id ON term_news(term_id);
CREATE INDEX IF NOT EXISTS idx_term_news_collected_at ON term_news(collected_at);
"""

RAW_DDL = """
CREATE TABLE IF NOT EXISTS raw_github (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at DATE    NOT NULL,
    repo_name    TEXT    NOT NULL,
    description  TEXT,
    topics       TEXT,
    stars        INTEGER NOT NULL DEFAULT 0,
    forks        INTEGER NOT NULL DEFAULT 0,
    stars_delta  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(collected_at, repo_name)
);

CREATE TABLE IF NOT EXISTS raw_hn (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at DATE    NOT NULL,
    hn_id        INTEGER NOT NULL,
    title        TEXT    NOT NULL,
    score        INTEGER NOT NULL DEFAULT 0,
    comments     INTEGER NOT NULL DEFAULT 0,
    timestamp    DATETIME,
    UNIQUE(collected_at, hn_id)
);
"""


def get_connection(db_path: Path = APP_DB) -> sqlite3.Connection:
    """コネクションを返す。デフォルトは app.db。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_raw_connection() -> sqlite3.Connection:
    """raw.db へのコネクションを返す。"""
    return get_connection(RAW_DB)


def init_db() -> None:
    """app.db と raw.db を冪等に初期化する。"""
    # app.db
    conn = get_connection(APP_DB)
    with conn:
        conn.executescript(APP_DDL)
        conn.executemany(
            "INSERT OR IGNORE INTO themes (theme_key, theme_name) VALUES (?, ?)",
            THEMES_SEED,
        )
    conn.close()

    # raw.db
    conn = get_connection(RAW_DB)
    with conn:
        conn.executescript(RAW_DDL)
    conn.close()

    print(f"[DB] Initialized: app.db + raw.db")


if __name__ == "__main__":
    init_db()
