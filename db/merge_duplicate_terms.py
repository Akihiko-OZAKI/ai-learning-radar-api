"""
重複用語マージスクリプト（2026-08-08 手動実行）

正規化（スペース・ハイフン・アンダースコア除去・小文字化）後に同一になる用語ペアを検出し、
スコアが高い方（または永続登録済みの方）を残して、もう一方を削除する。

方針:
  - is_permanent=1 の用語は削除しない（永続登録優先）
  - 両方 is_permanent=0 の場合、スコア合計が高い方を残す
  - 同スコアの場合、first_seen が古い方を残す
"""

import sys
import os
import re
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from db import get_connection


def normalize_term(name: str) -> str:
    return re.sub(r'[\s\-_]+', '', name.lower())


def main():
    conn = get_connection()

    # 全用語を取得
    terms = conn.execute("""
        SELECT t.term_id, t.term_name, t.is_permanent, t.first_seen,
               COALESCE(SUM(ds.total_score), 0) AS total_score_sum,
               COUNT(ds.date) AS score_days
        FROM terms t
        LEFT JOIN daily_scores ds ON t.term_id = ds.term_id
        GROUP BY t.term_id
    """).fetchall()

    # 正規化後でグループ化
    groups = defaultdict(list)
    for row in terms:
        norm = normalize_term(row["term_name"])
        groups[norm].append(dict(row))

    # 重複グループのみ抽出
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"重複グループ数: {len(dup_groups)}件\n")

    to_delete = []
    to_keep = []

    for norm, members in sorted(dup_groups.items()):
        # 永続登録済みを優先
        permanent = [m for m in members if m["is_permanent"] == 1]
        non_permanent = [m for m in members if m["is_permanent"] == 0]

        if permanent:
            # 永続登録済みを残す、それ以外を削除
            keep = permanent[0]  # 複数の場合は最初の1つ
            delete_list = [m for m in members if m["term_id"] != keep["term_id"]]
        else:
            # スコア合計が高い方を残す（同スコアなら古い方）
            members_sorted = sorted(
                members,
                key=lambda m: (-m["total_score_sum"], m["first_seen"])
            )
            keep = members_sorted[0]
            delete_list = members_sorted[1:]

        to_keep.append((norm, keep))
        to_delete.extend([(norm, d) for d in delete_list])

        print(f'  [{norm}]')
        print(f'    KEEP:   [{keep["term_id"]}] "{keep["term_name"]}" '
              f'(perm={keep["is_permanent"]}, score={keep["total_score_sum"]:.0f}, '
              f'first={keep["first_seen"]})')
        for d in delete_list:
            print(f'    DELETE: [{d["term_id"]}] "{d["term_name"]}" '
                  f'(perm={d["is_permanent"]}, score={d["total_score_sum"]:.0f}, '
                  f'first={d["first_seen"]})')

    print(f"\n削除対象: {len(to_delete)}件")
    confirm = input("削除を実行しますか？ (yes/no): ").strip().lower()
    if confirm != "yes":
        print("キャンセルしました。")
        conn.close()
        return

    deleted = 0
    with conn:
        for norm, d in to_delete:
            if d["is_permanent"] == 1:
                print(f"  ⚠️  SKIP (permanent): {d['term_name']}")
                continue
            conn.execute("DELETE FROM daily_scores WHERE term_id=?", (d["term_id"],))
            conn.execute("DELETE FROM term_news WHERE term_id=?", (d["term_id"],))
            conn.execute("DELETE FROM terms WHERE term_id=?", (d["term_id"],))
            print(f"  ✅ Deleted: [{d['term_id']}] {d['term_name']}")
            deleted += 1

    total = conn.execute("SELECT COUNT(*) FROM terms").fetchone()[0]
    print(f"\n合計 {deleted} 件削除しました。残り用語数: {total} 件")
    conn.close()


if __name__ == "__main__":
    main()
