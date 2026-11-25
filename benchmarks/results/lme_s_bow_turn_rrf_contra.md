# LongMemEval 检索评测

- 数据：`benchmarks/data/longmemeval_s_cleaned.json`
- 题数：470（已按官方做法跳过 30 道拒答题）
- 嵌入：`bow`　粒度：`turn`　内容：用户 + 助手
- 生成时间：2026-11-25 19:00

## 会话级召回

| 配置 | R_any@1 | R_any@5 | R_any@10 | R_all@5 | R_all@10 | nDCG@10 | MRR |
|---|---|---|---|---|---|---|---|
| rrf |  86.8% |  94.9% |  98.3% |  81.3% |  85.7% | 0.881 | 0.905 |

## 轮次级召回

| 配置 | R_any@5 | R_any@10 | R_all@10 | nDCG@10 | MRR |
|---|---|---|---|---|---|
| rrf |  83.2% |  88.9% |  68.7% | 0.647 | 0.657 |

## 开销

| 配置 | 写入 p50 (ms) | 写入 p95 (ms) | 检索 p50 (ms) | 检索 p95 (ms) | top-5 上下文 token | 每题记忆数 / 写入单元 |
|---|---|---|---|---|---|---|
| rrf | 2.3 | 14.4 | 54.4 | 79.0 | 1057 | 492.8 / 492.8 |

嵌入按题批量预计算，各配置共用，平均 0.54 ms/条；上表写入延迟不含嵌入时间。

## 按题型（会话级 R_any@5）

| 题型 | 题数 | rrf |
|---|---|---|
| knowledge-update | 72 |  98.6% |
| multi-session | 121 |  96.7% |
| single-session-assistant | 56 |  98.2% |
| single-session-preference | 30 |  63.3% |
| single-session-user | 64 |  98.4% |
| temporal-reasoning | 127 |  95.3% |
