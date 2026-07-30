"""
ノイズ用語クリーニングスクリプト

LLMを使って各用語が「AI技術用語として適切か」を判定し、
ノイズ用語（AIを使ったアプリ名、一般名詞、無関係な固有名詞など）を削除する。

判定基準:
  KEEP: LLMモデル名、AIフレームワーク、AIプロトコル、AI開発ツール、
        RAGシステム、AIエージェントフレームワーク、マルチモーダルAI技術
  DELETE: AIを使ったアプリ/サービス名、一般名詞、無関係な固有名詞、
          特定企業の製品名（AI技術そのものでないもの）

永続登録済み（is_permanent=1）の用語は対象外。
"""

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import anthropic
from db import get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

client = anthropic.Anthropic()
LLM_MODEL = "claude-haiku-4-5"
BATCH_SIZE = 50  # 1回のLLM呼び出しで判定する用語数


JUDGE_PROMPT = """あなたはAI技術トレンドの専門家です。
以下の用語リストについて、各用語が「AI技術・研究の文脈で重要な固有名詞」かどうかを判定してください。

## KEEP（残す）の基準
- LLMモデル名（GPT, Claude, Llama, Gemini, Mistral等）
- AIフレームワーク・ライブラリ（LangChain, LangGraph, PyTorch等）
- AIプロトコル・規格（MCP, A2A等）
- AI開発・推論インフラ（vLLM, Ollama, Groq等）
- RAG・検索技術（RAG, FAISS, Chroma等）
- AIエージェントフレームワーク（AutoGPT, CrewAI等）
- マルチモーダルAI技術（Sora, DALL-E等）
- AI評価・ベンチマーク（MMLU, HumanEval等）
- AI研究機関・主要企業名（Anthropic, OpenAI, Mistral AI等）

## DELETE（削除）の基準
- AIを使ったアプリ/サービス（料理アプリ、ライティングツール、ゲーム等）
- 一般的なプログラミングツール（Vim, Git等）
- 汎用インフラ（Docker, Kubernetes等）
- 無関係な固有名詞（人名、地名等）
- 意味不明な略語・造語
- 既存の有名ソフトウェアの派生品名（AIと無関係）

## 出力形式（JSONのみ）
{"results": [
  {"term": "用語名", "action": "KEEP" or "DELETE", "reason": "理由（10文字以内）"}
]}

## 判定対象
{terms_block}
"""


def judge_terms_batch(terms: list[dict]) -> list[dict]:
    """用語バッチをLLMで判定する。"""
    terms_block = "\n".join(
        f"- {t['term_name']} (theme={t['theme_name']}, category={t['category']})"
        for t in terms
    )
    prompt = JUDGE_PROMPT.replace("{terms_block}", terms_block)

    try:
        message = client.messages.create(
            model=LLM_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        content = message.content[0].text if message.content else '{"results":[]}'
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            content = content[start:end]
        parsed = json.loads(content)
        return parsed.get("results", [])
    except Exception as e:
        logger.error(f"LLM判定エラー: {e}")
        return []


def run_cleaning(dry_run: bool = True):
    """
    ノイズ用語を判定・削除する。

    Args:
        dry_run: True の場合は削除せず結果のみ表示
    """
    conn = get_connection()

    # 永続登録なしの用語を取得
    terms = conn.execute("""
        SELECT t.term_id, t.term_name, th.theme_name, t.category
        FROM terms t
        LEFT JOIN themes th ON t.theme_id = th.theme_id
        WHERE t.is_permanent = 0
        ORDER BY t.first_seen DESC
    """).fetchall()

    logger.info(f"判定対象: {len(terms)}件")

    delete_ids = []
    keep_ids = []
    total_batches = (len(terms) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(terms), BATCH_SIZE):
        batch = [dict(t) for t in terms[i:i + BATCH_SIZE]]
        batch_num = i // BATCH_SIZE + 1
        logger.info(f"バッチ {batch_num}/{total_batches} 処理中...")

        results = judge_terms_batch(batch)

        # 結果をterm_idと照合
        result_map = {r["term"]: r for r in results}
        for term in batch:
            result = result_map.get(term["term_name"])
            if result and result["action"] == "DELETE":
                delete_ids.append(term["term_id"])
                logger.debug(f"  DELETE: {term['term_name']} ({result.get('reason', '')})")
            else:
                keep_ids.append(term["term_id"])

        # レート制限対策
        time.sleep(0.5)

    logger.info(f"\n=== 判定結果 ===")
    logger.info(f"  KEEP:   {len(keep_ids)}件")
    logger.info(f"  DELETE: {len(delete_ids)}件")

    if dry_run:
        logger.info("\n[DRY RUN] 実際の削除は行いません。")
        # 削除予定の用語を表示
        if delete_ids:
            placeholders = ",".join("?" * len(delete_ids[:50]))
            sample = conn.execute(
                f"SELECT term_name FROM terms WHERE term_id IN ({placeholders})",
                delete_ids[:50],
            ).fetchall()
            logger.info(f"削除予定サンプル（最大50件）:")
            for r in sample:
                logger.info(f"  - {r[0]}")
    else:
        # 実際に削除
        if delete_ids:
            with conn:
                # daily_scoresも削除
                placeholders = ",".join("?" * len(delete_ids))
                conn.execute(
                    f"DELETE FROM daily_scores WHERE term_id IN ({placeholders})",
                    delete_ids,
                )
                conn.execute(
                    f"DELETE FROM term_news WHERE term_id IN ({placeholders})",
                    delete_ids,
                )
                conn.execute(
                    f"DELETE FROM terms WHERE term_id IN ({placeholders})",
                    delete_ids,
                )
            logger.info(f"\n{len(delete_ids)}件のノイズ用語を削除しました。")

    conn.close()
    return delete_ids


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="実際に削除を実行する")
    args = parser.parse_args()

    run_cleaning(dry_run=not args.execute)
