# LongMemEval 检索评测

- 数据：`benchmarks/data/longmemeval_s_cleaned.json`
- 题数：470（已按官方做法跳过 30 道拒答题）
- 嵌入：`bow`　粒度：`session`　内容：用户 + 助手
- 生成时间：2026-11-25 07:11

## 会话级召回

| 配置 | R_any@1 | R_any@5 | R_any@10 | R_all@5 | R_all@10 | nDCG@10 | MRR |
|---|---|---|---|---|---|---|---|
| vector |  50.0% |  75.3% |  84.9% |  48.1% |  61.7% | 0.603 | 0.611 |
| hybrid |  47.9% |  74.7% |  84.9% |  45.3% |  61.7% | 0.586 | 0.593 |

## 开销

| 配置 | 写入 p50 (ms) | 写入 p95 (ms) | 检索 p50 (ms) | 检索 p95 (ms) | top-5 上下文 token | 每题记忆数 / 写入单元 |
|---|---|---|---|---|---|---|
| vector | 5.6 | 8.0 | 2.1 | 2.4 | 13855 | 47.7 / 47.7 |
| hybrid | 5.7 | 8.5 | 2.1 | 2.5 | 13796 | 47.7 / 47.7 |

## 按题型（会话级 R_any@5）

| 题型 | 题数 | vector | hybrid |
|---|---|---|---|
| knowledge-update | 72 |  83.3% |  87.5% |
| multi-session | 121 |  78.5% |  79.3% |
| single-session-assistant | 56 |  94.6% |  92.9% |
| single-session-preference | 30 |  26.7% |  23.3% |
| single-session-user | 64 |  68.8% |  67.2% |
| temporal-reasoning | 127 |  74.0% |  70.9% |
