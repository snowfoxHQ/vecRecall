# 评测

两套评测，指标定义统一放在 `_metrics.py`。

| 脚本 | 用途 | 数据 | 耗时 |
|---|---|---|---|
| `benchmark_recall.py` | 快速冒烟，确认没改坏 | 内置 21 组 query + 15 条干扰 | 秒级 |
| `longmemeval.py` | 正式检索评测，作为版本对比基线 | LongMemEval（需下载） | 分钟到小时 |

## 内置评测

```bash
python benchmarks/benchmark_recall.py          # 词法嵌入（零依赖）
python benchmarks/benchmark_recall.py --all    # 加上语义嵌入
```

## LongMemEval

[LongMemEval](https://github.com/xiaowu0162/LongMemEval)（ICLR 2025）有 500 道题，每道题配一段带时间戳的多会话聊天历史，并标注答案在哪些会话、哪些轮次。这里**只评检索**：答案会话能不能排到前面。问答正确率需要 LLM 判分，见官方仓库。

按官方做法跳过 30 道拒答题（`_abs` 结尾，没有答案位置）。

### 1. 下载数据

从 [Hugging Face](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) 下载，放到 `benchmarks/data/`（该目录已在 .gitignore 中忽略，数据不进仓库）：

```bash
mkdir -p benchmarks/data && cd benchmarks/data
curl -LO https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json
cd ../..
```

| 文件 | 每题会话数 | 建议 |
|---|---|---|
| `longmemeval_s_cleaned.json` | 约 40 | 主评测集 |
| `longmemeval_m_cleaned.json` | 约 500 | 规模压力测试，很慢 |
| `longmemeval_oracle.json` | 只含证据会话 | 太简单，只作上限参考 |

### 2. 先用样例确认能跑

```bash
python benchmarks/longmemeval.py --data benchmarks/longmemeval_sample.json --configs vector,hybrid
```

### 3. 正式运行

```bash
# 抽 50 题快速看趋势
python benchmarks/longmemeval.py --data benchmarks/data/longmemeval_s_cleaned.json --limit 50

# 全量 + 消融，输出报告
python benchmarks/longmemeval.py --data benchmarks/data/longmemeval_s_cleaned.json \
    --configs vector,hybrid,dedup,graph --out benchmarks/results/lme_s_bow.md

# 语义嵌入（需 pip install -e ".[sentence]"）
python benchmarks/longmemeval.py --data benchmarks/data/longmemeval_s_cleaned.json \
    --embedder sentence --configs vector,hybrid --out benchmarks/results/lme_s_sentence.md
```

### 配置

| 配置 | 含义 |
|---|---|
| `vector` | 纯向量检索（默认路径，去重关闭） |
| `hybrid` | 相似度 + 重要性 + 时间新鲜度重排 |
| `dedup` | 开启写入时语义去重（阈值 0.92） |
| `graph` | 启发式抽取实体入图，用多跳检索（直接命中 + 图谱扩展）排序 |

其他参数：`--granularity turn` 每轮对话一条记忆（同时报告轮次级召回），`--user-only` 只写入用户轮次，`--types` 只评某些题型，`--dump` 输出逐题结果。

### 指标

| 指标 | 含义 |
|---|---|
| R_any@k | 前 k 个里至少有一个答案会话（官方主指标） |
| R_all@k | 前 k 个里包含全部答案会话（多会话题更严格） |
| nDCG@10 | 命中越靠前分越高 |
| MRR | 第一个答案会话排名的倒数 |
| 写入 / 检索延迟 | p50、p95，单位毫秒 |
| top-5 上下文 token | 把前 5 条结果放进 prompt 的大致 token 数 |

### 注意

- 每道题单独建一个临时库，题与题之间互不影响。
- 默认的词法嵌入是零依赖的下限，不代表语义嵌入的表现。对外公布数字时请注明嵌入后端和配置。
- 结果文件放在 `benchmarks/results/`，可以提交进仓库作为版本对比基线。
