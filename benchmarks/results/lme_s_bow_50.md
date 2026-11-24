# LongMemEval 检索评测

- 数据：`benchmarks/data/longmemeval_s_cleaned.json`
- 题数：50（已按官方做法跳过 30 道拒答题），随机抽样 seed=42
- 嵌入：`bow`　粒度：`session`　内容：用户 + 助手
- 生成时间：2026-11-25 04:58

## 会话级召回

| 配置 | R_any@1 | R_any@5 | R_any@10 | R_all@5 | R_all@10 | nDCG@10 | MRR |
|---|---|---|---|---|---|---|---|
| vector |  56.0% |  80.0% |  84.0% |  50.0% |  64.0% | 0.646 | 0.669 |
| hybrid |  56.0% |  80.0% |  84.0% |  50.0% |  64.0% | 0.646 | 0.669 |

## 开销

| 配置 | 写入 p50 (ms) | 写入 p95 (ms) | 检索 p50 (ms) | 检索 p95 (ms) | top-5 上下文 token | 每题记忆数 / 写入单元 |
|---|---|---|---|---|---|---|
| vector | 5.9 | 8.5 | 2.0 | 2.5 | 13293 | 47.7 / 47.7 |
| hybrid | 5.8 | 8.3 | 2.1 | 2.3 | 13293 | 47.7 / 47.7 |

## 按题型（会话级 R_any@5）

| 题型 | 题数 | vector | hybrid |
|---|---|---|---|
| knowledge-update | 9 |  88.9% |  88.9% |
| multi-session | 14 |  71.4% |  71.4% |
| single-session-assistant | 4 | 100.0% | 100.0% |
| single-session-preference | 3 |  33.3% |  33.3% |
| single-session-user | 10 | 100.0% | 100.0% |
| temporal-reasoning | 10 |  70.0% |  70.0% |
