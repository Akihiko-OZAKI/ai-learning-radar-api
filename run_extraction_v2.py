"""
用語抽出 v2 - フィルタリング強化版

改善点:
1. LLMへの入力をHNタイトル + GitHubトップ200件のdescriptionに絞る
   （topics全件は汎用語が多すぎるため除外）
2. 抽出後に「出現頻度フィルタ」を適用（2件以上のデータに登場する用語のみ採用）
3. 除外リスト（ノイズワード）を強化
4. 説明文生成は「スコア上位見込み用語」のみに限定
5. 最大登録数を200件に制限（PoC段階）
"""
import sys, os, json, logging, time, re
from collections import Counter
sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from datetime import date
from openai import OpenAI
from db import get_connection

TODAY = str(date.today())
client = OpenAI()
LLM_MODEL = "gpt-4.1-mini"
MAX_TERMS = 200  # PoC段階の最大登録数

# ── 除外リスト（汎用語・インフラ語・ノイズ） ──────────────────
EXCLUDE_TERMS = {
    # 汎用インフラ
    "docker", "kubernetes", "linux", "ubuntu", "windows", "macos",
    "python", "javascript", "typescript", "rust", "go", "java", "c++",
    "git", "github", "gitlab", "npm", "pip", "conda",
    # 汎用ML（古典的）
    "cnn", "rnn", "lstm", "neural network", "neural networks",
    "machine learning", "deep learning", "computer vision",
    "natural language processing", "nlp",
    # 汎用ツール
    "jupyter", "notebook", "pandas", "numpy", "scikit-learn",
    "matplotlib", "tensorflow", "keras",
    # 汎用クラウド
    "aws", "gcp", "azure", "s3", "lambda",
    # トークナイザ等（内部実装）
    "cl100k-base", "bpe", "tokenizer", "tokenization",
    # その他ノイズ
    "api", "sdk", "cli", "gui", "ui", "ux", "rest", "graphql",
    "json", "yaml", "toml", "csv", "sql", "nosql",
    "caffe", "caffe2", "theano", "mxnet",
    "comet-ml", "wandb", "mlflow",  # MLOpsツールは別途判断
    "clip",  # 文脈によるが汎用的すぎる
    "dalle",  # DALL-Eが正式名
}

THEME_OPTIONS = [
    "llm", "ai_coding", "ai_agent", "tool_integration",
    "retrieval", "ai_infra", "multimodal", "ai_framework", "other",
]
CATEGORY_OPTIONS = ["Model", "Tool", "Framework", "Protocol", "Agent", "Library", "Other"]


def get_known_terms() -> set[str]:
    conn = get_connection()
    rows = conn.execute("SELECT term_name FROM terms").fetchall()
    conn.close()
    return {r["term_name"].lower() for r in rows}


def get_theme_id(theme_key: str):
    conn = get_connection()
    row = conn.execute("SELECT theme_id FROM themes WHERE theme_key=?", (theme_key,)).fetchone()
    conn.close()
    return row["theme_id"] if row else None


def collect_texts() -> tuple[list[str], dict[str, int]]:
    """
    テキストを収集し、各テキストに含まれる用語の出現カウント用の
    「テキストリスト」と「生テキスト集合」を返す。
    """
    conn = get_connection()

    # HNタイトル（全件）
    hn_rows = conn.execute(
        "SELECT title FROM raw_hn WHERE collected_at=?", (TODAY,)
    ).fetchall()

    # GitHub: スター上位500件のdescription + repo_name
    gh_rows = conn.execute(
        """
        SELECT repo_name, description, topics, stars
        FROM raw_github
        WHERE collected_at=?
          AND description IS NOT NULL AND description != ''
        ORDER BY stars DESC
        LIMIT 500
        """,
        (TODAY,),
    ).fetchall()

    conn.close()

    texts = []

    # HNタイトル
    for row in hn_rows:
        if row["title"]:
            texts.append(row["title"])

    # GitHub（repo_name + description、80件ずつまとめる）
    gh_items = []
    for row in gh_rows:
        name = row["repo_name"].split("/")[-1]
        desc = (row["description"] or "").strip()
        if desc:
            gh_items.append(f"{name}: {desc}")

    # 80件ずつチャンクにまとめてLLMに渡す
    for i in range(0, len(gh_items), 80):
        texts.append("\n".join(gh_items[i:i+80]))

    logger.info(f"HN: {len(hn_rows)} titles, GitHub: {len(gh_items)} descriptions → {len(texts)} text chunks")
    return texts


def extract_with_llm(texts: list[str], known_terms: set[str]) -> list[dict]:
    """LLMで用語を抽出する（チャンクサイズ80）。"""
    known_sample = ", ".join(sorted(known_terms)[:40]) if known_terms else "（なし）"
    theme_list = ", ".join(THEME_OPTIONS)
    category_list = ", ".join(CATEGORY_OPTIONS)
    results = []

    for i in range(0, len(texts), 80):
        chunk = texts[i:i+80]
        texts_block = "\n".join(f"- {t[:200]}" for t in chunk)  # 1行200文字に制限

        prompt = f"""あなたはAI技術トレンドの専門家です。
以下のテキストから、**現在注目されているAI技術の固有名詞・ツール名・モデル名・フレームワーク名**のみを抽出してください。

## 抽出基準（厳格に適用）
- 対象: LLM、生成AI、AIエージェント、AIコーディングツール、AIプロトコル、RAGシステム、マルチモーダルAI
- 対象: 具体的な製品名・サービス名・技術名（例: Claude Code, LangGraph, MCP, Cursor, vLLM）
- 除外: 汎用プログラミング言語（Python, JavaScript等）
- 除外: 汎用インフラ（Docker, Kubernetes, AWS等）
- 除外: 古典的MLアルゴリズム（CNN, RNN, LSTM等）
- 除外: 既知用語: {known_sample}
- 1〜4単語の固有名詞のみ

## 出力形式（JSONのみ）
{{"terms": [
  {{"term": "用語名", "theme": "テーマキー", "category": "カテゴリ"}}
]}}
テーマキー: {theme_list}
カテゴリ: {category_list}

## テキスト
{texts_block}"""

        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or '{"terms":[]}'
            parsed = json.loads(content)
            items = parsed.get("terms", [])
            if not items:
                for v in parsed.values():
                    if isinstance(v, list):
                        items = v
                        break
            results.extend(items)
            logger.info(f"  Chunk {i//80+1}: {len(items)} terms extracted")
        except Exception as e:
            logger.error(f"  Chunk {i//80+1} error: {e}")

    return results


def generate_description(term: str) -> str:
    """用語の説明文を生成する。"""
    prompt = f"AI技術用語「{term}」について、日本語で100〜200文字の簡潔な説明文を書いてください。説明文のみ返してください。"
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"Description error for {term}: {e}")
        return ""


def run():
    known_terms = get_known_terms()
    logger.info(f"Known terms: {len(known_terms)}")

    texts = collect_texts()
    logger.info(f"Total text chunks: {len(texts)}")

    # LLM抽出
    logger.info("=== LLM Extraction ===")
    extracted = extract_with_llm(texts, known_terms)
    logger.info(f"Raw extracted: {len(extracted)}")

    # 出現頻度カウント（同じ用語が複数チャンクで出現したものを優先）
    term_count: Counter = Counter()
    term_meta: dict[str, dict] = {}
    for item in extracted:
        name = (item.get("term") or "").strip()
        if not name:
            continue
        key = name.lower()
        term_count[key] += 1
        if key not in term_meta:
            term_meta[key] = item

    # フィルタリング
    filtered = []
    for key, count in term_count.most_common():
        name = term_meta[key]["term"].strip()
        name_lower = name.lower()

        # 除外リストチェック
        if name_lower in EXCLUDE_TERMS:
            continue
        # 既知用語スキップ
        if name_lower in known_terms:
            continue
        # 短すぎる用語スキップ
        if len(name) < 2:
            continue
        # 数字のみスキップ
        if re.match(r'^[\d\.\-]+$', name):
            continue

        filtered.append((name, term_meta[key], count))

    logger.info(f"After filtering: {len(filtered)} terms")

    # 出現頻度順にソートし、上位MAX_TERMSのみ採用
    filtered.sort(key=lambda x: x[2], reverse=True)
    to_register = filtered[:MAX_TERMS]
    logger.info(f"To register (top {MAX_TERMS}): {len(to_register)} terms")

    # DB登録（説明文生成付き）
    conn = get_connection()
    registered = 0
    with conn:
        for i, (term_name, item, count) in enumerate(to_register):
            theme_key = item.get("theme", "other")
            category = item.get("category", "Other")
            theme_id = get_theme_id(theme_key)

            logger.info(f"  [{i+1}/{len(to_register)}] {term_name} (count={count}, theme={theme_key})")
            description = generate_description(term_name)

            conn.execute(
                """
                INSERT OR IGNORE INTO terms
                    (term_name, theme_id, category, first_seen, last_seen,
                     description, is_permanent)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (term_name, theme_id, category, TODAY, TODAY, description),
            )
            registered += 1
            time.sleep(0.05)

    conn.close()

    # 最終確認
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM terms").fetchone()[0]
    # テーマ別内訳
    theme_counts = conn.execute(
        """
        SELECT th.theme_name, COUNT(*) as c
        FROM terms t
        LEFT JOIN themes th ON t.theme_id = th.theme_id
        GROUP BY th.theme_id ORDER BY c DESC
        """
    ).fetchall()
    conn.close()

    print(f"\n{'='*50}")
    print(f"[抽出完了]")
    print(f"  新規登録: {registered} 件")
    print(f"  辞書DB合計: {total} 件")
    print(f"\n[テーマ別内訳]")
    for r in theme_counts:
        print(f"  {r['theme_name']:22}: {r['c']:3} 件")
    print(f"{'='*50}")


if __name__ == "__main__":
    run()
