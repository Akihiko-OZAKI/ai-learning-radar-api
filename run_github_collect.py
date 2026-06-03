"""GitHub フル収集スクリプト"""
import sys, os, logging
sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

import collectors.github_collector as gh_mod
from db import init_db, get_connection

gh_mod.AI_TOPICS = [
    "llm", "large-language-model", "language-model",
    "gpt", "chatgpt", "openai", "claude", "anthropic",
    "gemini", "mistral", "llama", "deepseek", "grok",
    "ai-agent", "autonomous-agent", "multi-agent",
    "langchain", "langgraph", "crewai", "autogen",
    "openai-agents", "smolagents",
    "rag", "retrieval-augmented-generation",
    "vector-database", "embedding",
    "mcp", "model-context-protocol",
    "function-calling", "tool-use",
    "ai-coding", "copilot", "cursor", "claude-code",
    "code-generation",
    "llm-inference", "vllm", "ollama", "mlops",
    "ai-deployment", "model-serving",
    "multimodal", "vision-language-model", "text-to-image",
    "stable-diffusion", "diffusion-model",
    "generative-ai", "machine-learning", "deep-learning",
    "huggingface", "transformers", "pytorch",
]
gh_mod.PER_PAGE = 100
gh_mod.MAX_PAGES = 3

init_db()
github_token = os.environ.get("GITHUB_TOKEN")
count = gh_mod.collect(github_token=github_token)

conn = get_connection()
total = conn.execute("SELECT COUNT(*) FROM raw_github").fetchone()[0]
conn.close()
print(f"\nGitHub saved: {count} repos (total in DB: {total})")
