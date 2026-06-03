"""
AI変化観測所 - 日次バッチ実行エントリーポイント

実行方法:
  cd /home/ubuntu/ai_observatory
  python3 main.py [--skip-collect] [--skip-extract] [--show-ranking]

環境変数:
  OPENAI_API_KEY   : OpenAI API キー（LLM 抽出に使用）
  GITHUB_TOKEN     : GitHub Personal Access Token（レート制限緩和、任意）
"""

import argparse
import logging
import os
import sys

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(__file__))

from db import init_db
from collectors import collect_github, collect_hn
from extractor import run_extraction
from scorer import run_scoring, print_rankings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="AI変化観測所 日次バッチ")
    parser.add_argument(
        "--skip-collect", action="store_true", help="データ収集をスキップする"
    )
    parser.add_argument(
        "--skip-extract", action="store_true", help="LLM用語抽出をスキップする"
    )
    parser.add_argument(
        "--show-ranking", action="store_true", help="ランキングをコンソールに表示する"
    )
    args = parser.parse_args()

    github_token = os.environ.get("GITHUB_TOKEN")

    # ── Step 1: DB 初期化（冪等） ──────────────────────────────
    logger.info("Step 1/4: DB 初期化")
    init_db()

    # ── Step 2: データ収集 ────────────────────────────────────
    if not args.skip_collect:
        logger.info("Step 2/4: データ収集 - GitHub")
        gh_count = collect_github(github_token=github_token)
        logger.info(f"  → GitHub: {gh_count} repos 保存")

        logger.info("Step 2/4: データ収集 - Hacker News")
        hn_count = collect_hn()
        logger.info(f"  → HN: {hn_count} articles 保存")
    else:
        logger.info("Step 2/4: データ収集 スキップ")

    # ── Step 3: LLM 用語抽出（新規用語発見） ──────────────────
    if not args.skip_extract:
        logger.info("Step 3/4: LLM 用語抽出")
        new_terms = run_extraction()
        logger.info(f"  → 新規用語: {new_terms} 件登録")
    else:
        logger.info("Step 3/4: LLM 用語抽出 スキップ")

    # ── Step 4: 決定論的スコアリング＋ランキング生成 ──────────
    logger.info("Step 4/4: スコアリング＋ランキング生成")
    scored = run_scoring()
    logger.info(f"  → {scored} 用語のスコアを記録")

    # ── オプション: ランキング表示 ────────────────────────────
    if args.show_ranking:
        print_rankings()

    logger.info("バッチ完了")


if __name__ == "__main__":
    main()
