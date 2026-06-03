"""
初回フル収集スクリプト

GitHub: 全AIトピック × 最大3ページ（最大300件/トピック）
HN:     topstories 500件 + beststories 200件
"""
import sys, os, logging
sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from db import init_db, get_connection
import collectors.github_collector as gh_mod
import collectors.hn_collector as hn_mod

# ── 設定 ────────────────────────────────────────────────────
# GitHub: 収集対象トピックを拡充
gh_mod.AI_TOPICS = [
    # LLM / モデル
    "llm", "large-language-model", "language-model",
    "gpt", "chatgpt", "openai", "claude", "anthropic",
    "gemini", "mistral", "llama", "deepseek", "grok",
    # エージェント
    "ai-agent", "autonomous-agent", "multi-agent",
    "langchain", "langgraph", "crewai", "autogen",
    "openai-agents", "smolagents",
    # RAG / 検索
    "rag", "retrieval-augmented-generation",
    "vector-database", "embedding",
    # ツール / プロトコル
    "mcp", "model-context-protocol",
    "function-calling", "tool-use",
    # コーディング
    "ai-coding", "copilot", "cursor", "claude-code",
    "code-generation",
    # インフラ / デプロイ
    "llm-inference", "vllm", "ollama", "mlops",
    "ai-deployment", "model-serving",
    # マルチモーダル
    "multimodal", "vision-language-model", "text-to-image",
    "stable-diffusion", "diffusion-model",
    # フレームワーク
    "generative-ai", "machine-learning", "deep-learning",
    "huggingface", "transformers", "pytorch",
]
gh_mod.PER_PAGE = 100
gh_mod.MAX_PAGES = 3

# HN: 500件 + 200件
hn_mod.STORY_ENDPOINTS = {
    "topstories": 500,
    "beststories": 200,
}

# ── 実行 ────────────────────────────────────────────────────
init_db()

logger.info("=== GitHub 収集開始 ===")
github_token = os.environ.get("GITHUB_TOKEN")
gh_count = gh_mod.collect(github_token=github_token)
logger.info(f"GitHub: {gh_count} repos 保存完了")

logger.info("=== Hacker News 収集開始 ===")
hn_count = hn_mod.collect()
logger.info(f"HN: {hn_count} articles 保存完了")

# 確認
conn = get_connection()
print(f"\n[収集結果]")
print(f"  raw_github: {conn.execute('SELECT COUNT(*) FROM raw_github').fetchone()[0]} 件")
print(f"  raw_hn:     {conn.execute('SELECT COUNT(*) FROM raw_hn').fetchone()[0]} 件")
conn.close()
