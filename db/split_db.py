"""
DB分離スクリプト

observatory.db（全データ）を2つに分離する:
  - app.db    : アプリ用軽量DB（terms, themes, daily_scores）→ Gitに含める
  - raw.db    : 生データDB（raw_github, raw_hn）→ .gitignoreで除外

app.db は Render にデプロイされ、APIサーバーが参照する。
raw.db はローカルのみで保持し、バッチ処理に使用する。
"""

import sqlite3
import shutil
from pathlib import Path

DB_DIR = Path(__file__).parent
FULL_DB = DB_DIR / "observatory.db"
APP_DB  = DB_DIR / "app.db"
RAW_DB  = DB_DIR / "raw.db"


def split():
    if not FULL_DB.exists():
        print(f"[ERROR] {FULL_DB} not found")
        return

    # app.db: themes, terms, daily_scores のみ
    print(f"Creating {APP_DB} ...")
    if APP_DB.exists():
        APP_DB.unlink()

    src = sqlite3.connect(FULL_DB)
    dst = sqlite3.connect(APP_DB)

    # スキーマとデータをコピー
    for table in ["themes", "terms", "daily_scores"]:
        # テーブル定義を取得
        ddl = src.execute(
            f"SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        ).fetchone()
        if ddl and ddl[0]:
            dst.execute(ddl[0])
        # データをコピー
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        if rows:
            placeholders = ",".join(["?"] * len(rows[0]))
            dst.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        print(f"  {table}: {len(rows)} rows")

    dst.commit()
    dst.close()

    # raw.db: raw_github, raw_hn のみ
    print(f"Creating {RAW_DB} ...")
    if RAW_DB.exists():
        RAW_DB.unlink()

    dst = sqlite3.connect(RAW_DB)
    for table in ["raw_github", "raw_hn"]:
        ddl = src.execute(
            f"SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        ).fetchone()
        if ddl and ddl[0]:
            dst.execute(ddl[0])
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        if rows:
            placeholders = ",".join(["?"] * len(rows[0]))
            dst.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        print(f"  {table}: {len(rows)} rows")

    dst.commit()
    dst.close()
    src.close()

    print(f"\nDone.")
    print(f"  app.db : {APP_DB.stat().st_size / 1024:.1f} KB  ← Gitに含める")
    print(f"  raw.db : {RAW_DB.stat().st_size / 1024:.1f} KB  ← .gitignoreで除外")


if __name__ == "__main__":
    split()
