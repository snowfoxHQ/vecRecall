# LongMemEval 检索评测

- 数据：`benchmarks/data/longmemeval_s_cleaned.json`
- 题数：470（已按官方做法跳过 30 道拒答题）
- 嵌入：`bow`　粒度：`turn`　内容：用户 + 助手
- 生成时间：2026-11-25 07:36

## 会话级召回

| 配置 | R_any@1 | R_any@5 | R_any@10 | R_all@5 | R_all@10 | nDCG@10 | MRR |
|---|---|---|---|---|---|---|---|
| vector |  77.9% |  92.1% |  94.3% |  72.8% |  78.5% | 0.806 | 0.835 |

## 轮次级召回

| 配置 | R_any@5 | R_any@10 | R_all@10 | nDCG@10 | MRR |
|---|---|---|---|---|---|
| vector |  78.1% |  82.8% |  61.7% | 0.589 | 0.602 |

## 开销

| 配置 | 写入 p50 (ms) | 写入 p95 (ms) | 检索 p50 (ms) | 检索 p95 (ms) | top-5 上下文 token | 每题记忆数 / 写入单元 |
|---|---|---|---|---|---|---|
| vector | 2.6 | 4.3 | 17.8 | 21.1 | 893 | 492.8 / 492.8 |

## 按题型（会话级 R_any@5）

| 题型 | 题数 | vector |
|---|---|---|
| knowledge-update | 72 |  97.2% |
| multi-session | 121 |  94.2% |
| single-session-assistant | 56 |  98.2% |
| single-session-preference | 30 |  46.7% |
| single-session-user | 64 |  98.4% |
| temporal-reasoning | 127 |  92.1% |
