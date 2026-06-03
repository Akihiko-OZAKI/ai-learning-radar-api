# AI変化観測所 - バックエンド PoC v0.1

GitHubとHacker NewsからAI技術用語を自動抽出し、ランキングを生成するバックエンドシステム。

---

## ディレクトリ構成

```
ai_observatory/
├── main.py                  # 日次バッチ実行エントリーポイント
├── db/
│   └── schema.py            # DBスキーマ定義・初期化（SQLite）
├── collectors/
│   ├── github_collector.py  # GitHub API データ収集
│   └── hn_collector.py      # Hacker News API データ収集
├── extractor/
│   └── llm_extractor.py     # LLM による新規用語発見・テーマ付与
└── scorer/
    ├── scoring.py           # 決定論的スコアリング・上昇理由判定
    └── ranking.py           # ランキング取得・コンソール出力
```

---

## 実行方法

### 環境変数の設定

```bash
export OPENAI_API_KEY="sk-..."       # 必須（LLM用語抽出に使用）
export GITHUB_TOKEN="ghp_..."        # 任意（レート制限緩和）
```

### 日次バッチの実行

```bash
cd ai_observatory
python3 main.py --show-ranking
```

### オプション

| オプション | 説明 |
| :--- | :--- |
| `--skip-collect` | データ収集をスキップ（DB に既存データがある場合） |
| `--skip-extract` | LLM 用語抽出をスキップ |
| `--show-ranking` | 実行後にランキングをコンソール表示 |

---

## 処理フロー

```
[1] DB 初期化（冪等）
    ↓
[2] データ収集
    ├── GitHub API → raw_github テーブル
    └── HN API    → raw_hn テーブル
    ↓
[3] LLM 用語抽出（新規用語のみ）
    └── 辞書DBに未登録の AI 用語を発見 → terms テーブルに候補登録
    ↓
[4] 決定論的スコアリング（LLM 不使用）
    ├── GitHub スコア = stars_delta×1.0 + stars×0.001 + forks×0.1
    ├── HN スコア     = score×1.0 + comments×0.5 + post_count×10.0
    ├── 総合スコア    = GitHub×70% + HN×30%
    ├── 上昇理由判定  = 最大寄与指標を自動判定
    └── Top20 入り用語 → is_permanent=True で永続保存
```

---

## DBスキーマ概要

| テーブル | 役割 |
| :--- | :--- |
| `themes` | テーママスタ（LLM / AI Coding / AI Agent 等） |
| `terms` | **用語辞書（中核資産）** Top20入り用語は永続保存 |
| `daily_scores` | 日次スコア・ランキング・上昇理由 |
| `raw_github` | GitHub 生データ |
| `raw_hn` | Hacker News 生データ |

---

## スコア計算式（変更可能）

`scorer/scoring.py` の先頭定数を変更することで、スコア重みと係数を調整できます。

```python
GITHUB_WEIGHT = 0.7      # GitHub スコアの重み
HN_WEIGHT     = 0.3      # HN スコアの重み

GH_STARS_DELTA_COEF = 1.0   # Star 増加数の係数
GH_STARS_COEF       = 0.001 # 累積 Star 数の係数
GH_FORKS_COEF       = 0.1   # Fork 数の係数

HN_SCORE_COEF      = 1.0    # HN スコアの係数
HN_COMMENTS_COEF   = 0.5    # コメント数の係数
HN_POST_COUNT_COEF = 10.0   # 投稿件数の係数（1件あたり固定加算）
```
