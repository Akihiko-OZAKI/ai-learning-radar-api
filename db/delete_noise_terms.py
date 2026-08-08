"""
ノイズ用語削除スクリプト（2026-08-08 手動実行）

削除対象:
  - Google           : 汎用企業名（AI技術用語ではない）
  - Transformer Transformer : 重複・意味不明
  - Claude AI        : Claude と重複
  - Anthropic Claude : Claude と重複
  - ChatGPT API      : ChatGPT と重複
  - MCP-server       : MCP と重複
  - MCP Servers      : MCP と重複
  - Paddle           : PaddlePaddle の略称（曖昧）

残す:
  - DeepMind : AI研究機関として重要
  - CUDA     : AI/MLインフラとして重要
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db import get_connection

NOISE_TERMS = [
    'Google',
    'Transformer Transformer',
    'Claude AI',
    'Anthropic Claude',
    'ChatGPT API',
    'MCP-server',
    'MCP Servers',
    'Paddle',
]

def main():
    conn = get_connection()

    print("=== 削除前の確認 ===")
    for name in NOISE_TERMS:
        row = conn.execute(
            "SELECT term_id, term_name, is_permanent FROM terms WHERE LOWER(term_name)=LOWER(?)",
            (name,)
        ).fetchone()
        if row:
            if row["is_permanent"] == 1:
                print(f"  ⚠️  SKIP (permanent): {row['term_name']}")
            else:
                print(f"  🗑️  DELETE: [{row['term_id']}] {row['term_name']}")
        else:
            print(f"  (not found): {name}")

    confirm = input("\n削除を実行しますか？ (yes/no): ").strip().lower()
    if confirm != "yes":
        print("キャンセルしました。")
        conn.close()
        return

    deleted = 0
    with conn:
        for name in NOISE_TERMS:
            row = conn.execute(
                "SELECT term_id, is_permanent FROM terms WHERE LOWER(term_name)=LOWER(?)",
                (name,)
            ).fetchone()
            if not row:
                continue
            if row["is_permanent"] == 1:
                print(f"  ⚠️  SKIP (permanent): {name}")
                continue
            term_id = row["term_id"]
            # 関連データも削除
            conn.execute("DELETE FROM daily_scores WHERE term_id=?", (term_id,))
            conn.execute("DELETE FROM term_news WHERE term_id=?", (term_id,))
            conn.execute("DELETE FROM terms WHERE term_id=?", (term_id,))
            print(f"  ✅ Deleted: {name}")
            deleted += 1

    # ノイズ用語をextractor側のNOISE_TERMSに追加するよう促す
    print(f"\n合計 {deleted} 件削除しました。")
    total = conn.execute("SELECT COUNT(*) FROM terms").fetchone()[0]
    print(f"残り用語数: {total} 件")
    conn.close()


if __name__ == "__main__":
    main()
