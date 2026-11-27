# VecRecall

[![PyPI](https://img.shields.io/pypi/v/vecrecall)](https://pypi.org/project/vecrecall/)
[![CI](https://github.com/snowfoxHQ/vecRecall/actions/workflows/ci.yml/badge.svg)](https://github.com/snowfoxHQ/vecRecall/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10+-brightgreen)](https://python.org)
[![许可证](https://img.shields.io/badge/license-MIT-green)](https://github.com/snowfoxHQ/vecRecall/blob/main/LICENSE)

**本地优先的 AI 记忆库**。给 Agent 和 AI 应用存长期记忆：存原文、按需召回、只把相关的几百 token 放进 prompt。默认零依赖，数据全部在本机 SQLite 里；需要多 Agent 隔离、不可变审计记录时也能直接用。

English: [README_EN.md](https://github.com/snowfoxHQ/vecRecall/blob/main/README_EN.md) · 版本记录：[CHANGELOG.md](https://github.com/snowfoxHQ/vecRecall/blob/main/CHANGELOG.md)

| | 零依赖默认配置 | 装上语义模型 |
|---|---|---|
| LongMemEval-S 召回（R_any@5，470 题） | **94.7%** | **98.3%** |
| 每次召回放进 prompt 的上下文（top-5） | 约 1,100 token | 约 1,200 token |
| 额外依赖 | 无 | `sentence-transformers` |

数字可复现，方法见 [召回率](#召回率)。

---

## 安装

```bash
pip install vecrecall                    # 零依赖：词法嵌入 + BM25 + SQLite
pip install "vecrecall[sentence]"        # 加多语言语义模型（召回更高，写入时每条多一次编码）
pip install "vecrecall[fast]"            # numpy 加速向量检索（1 万条以上建议）
pip install "vecrecall[crypto]"          # 加静态加密
pip install "vecrecall[full]"            # 以上全部 + ChromaDB
```

从源码安装：`git clone https://github.com/snowfoxHQ/vecRecall && cd vecRecall && pip install -e .`

装了 `sentence-transformers` 时，`VecRecall()` 会自动用语义模型；没装就用零依赖的词法嵌入。也可以用 `embedding_backend=` 显式指定，见[后端](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#后端可插拔)。

---

## 5 分钟上手

```python
from vecrecall import VecRecall

with VecRecall(base_dir="~/.vr", wing="my-project") as palace:
    # 写入：原文逐字保存
    palace.add("决定用 PostgreSQL 替代 MySQL，原因是 PostgreSQL 的 JSON 支持更好",
               topic="database", importance=0.9)

    # 存档一段对话：默认每轮一条记忆，共用同一个 session_id
    palace.archive_session([
        {"role": "user", "content": "我刚领养了一只金毛，叫 Max"},
        {"role": "assistant", "content": "恭喜！先从笼内训练开始"},
    ], session_id="chat-0525")
    palace.search("我的狗叫什么名字", n=1)   # 命中「我刚领养了一只金毛，叫 Max」那一轮

    # 检索：默认 rrf（向量 + BM25 按名次融合）
    for r in palace.search("为什么用 PostgreSQL 不用 MySQL", n=5):
        print(r.score, r.memory.content)

    # 构建要放进 prompt 的上下文
    ctx = palace.build_context(current_query="PostgreSQL 的 JSON 支持怎么样", load_l3=True)
    print(ctx.total_tokens_estimate)
```

命令行：

```bash
vr init --dir ~/.vr --wing my-project

# 写入两条记忆
vr add "登录接口的 JWT 有效期从 1 小时改成 24 小时，refresh token 保持 30 天" --topic auth
vr add "订单服务的数据库连接池上限调到 100" --topic database

# 存档一段对话（文件格式见下方），默认每轮对话一条记忆
vr archive chat.json

# 检索：默认 rrf；可用 --mode vector|bm25|rrf 切换
vr search "JWT 有效期是多少"

# 生成要放进 prompt 的上下文：L1 关键记忆 + 与问题相关的记忆
vr context "JWT 有效期多长，要不要改" --l3
```

`chat.json` 是一个消息列表：

```json
[
  {"role": "user", "content": "测试环境的 Redis 地址改成 10.0.0.12 了"},
  {"role": "assistant", "content": "好的，已更新配置说明"}
]
```

接到 Claude Code 等 MCP 客户端：

```json
{ "mcpServers": { "vecrecall": { "command": "vr-mcp", "args": ["--dir", "~/.vr", "--wing", "my-project"] } } }
```

---

## 核心设计

**检索和组织分开**。`wing`（项目）、`topic`（话题）只用来浏览和整理，不参与检索过滤；检索永远在全部记忆上做。按目录过滤检索的做法，会把放错目录的相关记忆直接漏掉。

**存原文，摘要只给人看**。写入的内容逐字保存；`ui_summary` 等摘要只在列表里展示，不参与检索。

**按轮存储，按需召回**。会话默认每轮对话存一条记忆。和整段会话存一条相比，命中的那一轮不会被长会话里的其他内容稀释，召回的上下文也只有原来的约十二分之一。

**四层上下文**。一次唤醒只加载需要的部分：

```
L0  身份      约 50 token   每次都加载
L1  关键记忆  约 600 token  按重要性选取；不可变记忆始终包含
L2  语义触发  按需          与当前话题足够相关才加载
L3  深度检索  显式请求      默认 rrf 检索
```

**默认用 rrf 检索**。同时跑向量检索和 BM25 词法检索，只按名次合并（Reciprocal Rank Fusion），不直接比较两边的分数。语义模型擅长同义改写，BM25 擅长精确词匹配，两路结果互补。零依赖时比纯向量高 2.6 个百分点；装上语义模型后在各项指标上都超过单独的 BM25。`mode="vector"` 或 `mode="bm25"` 可以切换。

---

## 召回率

评测集是 [LongMemEval](https://github.com/xiaowu0162/LongMemEval)（ICLR 2025）的 S 版：500 道题，每道题配一段约 48 个会话、约 490 轮对话的历史，并标注答案所在的会话。按官方做法跳过 30 道拒答题，评 470 道；只评检索，不评问答。

| 配置（每轮一条记忆） | 嵌入 | R_any@1 | R_any@5 | R_any@10 | MRR | 偏好类 R_any@5 |
|--|--|--|--|--|--|--|
| `rrf`（默认） | 语义 `sentence` | **91.3%** | **98.3%** | **98.9%** | **0.944** | **96.7%** |
| `vector` | 语义 `sentence` | 85.7% | 96.4% | 97.0% | 0.904 | 90.0% |
| `rrf`（默认） | 检索模型 `e5-small` | 91.1% | 98.9% | 99.4% | 0.945 | 100.0% |
| `vector` | 检索模型 `e5-small` | 90.9% | 98.3% | 99.4% | 0.941 | 90.0% |
| `bm25` | 不需要 | 87.9% | 96.6% | 98.1% | 0.914 | 80.0% |
| `rrf`（默认） | 词法 `bow`（零依赖） | 87.0% | 94.7% | 98.3% | 0.907 | 66.7% |
| `vector` | 词法 `bow` | 77.9% | 92.1% | 94.3% | 0.835 | 46.7% |
| `vector`，整段会话一条（v2.2 默认） | 词法 `bow` | 50.0% | 75.3% | 84.9% | 0.611 | 26.7% |

- R_any@k：前 k 条结果里至少有一个答案会话。会话级召回，把检索到的记忆换算成所属会话再去重。
- 语义模型是默认的 `paraphrase-multilingual-MiniLM-L12-v2`。在普通桌面 CPU 上编码约 14 ms/条，就是写入时多出来的时间。
- `e5-small` 是检索专用模型 `intfloat/multilingual-e5-small`（多语言，查询和文档自动加 `query:` / `passage:` 前缀），编码约 35 ms/条，是默认模型的 2.5 倍。默认的 `rrf` 下它和默认模型基本打平；主要用 `mode="vector"` 时提升明显（R_any@1 85.7% → 90.9%）。开启：`--embedder sentence --embed-model intfloat/multilingual-e5-small`，或 `build_embedding_backend("sentence", model="intfloat/multilingual-e5-small")`；已有数据换模型后要 `vr reindex`。
- 偏好类只有 30 题，误差约 ±4 个百分点；其余题型见原始报告。

复现：

```bash
python benchmarks/longmemeval.py --data benchmarks/data/longmemeval_s_cleaned.json \
    --granularity turn --configs vector,bm25,rrf [--embedder sentence]
```

下载数据、全部参数和按题型的结果见 [benchmarks/README.md](https://github.com/snowfoxHQ/vecRecall/blob/main/benchmarks/README.md)，原始结果在 [benchmarks/results/](https://github.com/snowfoxHQ/vecRecall/tree/main/benchmarks/results/)。

---

## 规模与性能

默认配置（SQLite 向量后端 + 词法嵌入，已安装 numpy）在不同记忆数下的延迟，单位毫秒，p50：

| 记忆数 | `rrf` 检索（默认） | `bm25` 检索 | `vector` 检索 | 写入（不去重） | 写入（`add()` 默认去重） | 新进程首次检索 | 磁盘 |
|---|---|---|---|---|---|---|---|
| 1,000 | 0.9 | 0.3 | 0.5 | 4.2 | 4.3 | 38 | 6 MB |
| 10,000 | 4.1 | 2.3 | 1.3 | 4.7 | 5.7 | 431 | 56 MB |
| 50,000 | 21 | 14 | 5.6 | 4.7 | 9.7 | 2,557 | 286 MB |
| 100,000 | 45 | 30 | 11 | 4.7 | 16 | 5,612 | 603 MB |

与 v2.3 相比（10 万条）：`rrf` 检索 4,907 → 45 ms（约 110 倍），`vector` 检索 4,631 → 11 ms（约 420 倍），默认去重写入 4,324 → 16 ms（约 270 倍），新进程首次检索 14 秒以上 → 5.6 秒。

- 向量在首次检索时载入内存，之后增量维护；装了 numpy 时用矩阵计算。`pip install "vecrecall[fast]"` 会装上 numpy；没装时自动退回纯 Python，比 v2.3 快约 2–3 倍，但仍随记忆数线性增长，1 万条以上建议安装。
- 「新进程首次检索」对应每次运行 `vr` 命令：要载入向量并构建词法索引。分词结果有缓存，只有新增或修改过的记忆才重新分词。MCP / REST 这类常驻服务只在启动后第一次检索时付这个时间。只需要向量检索时可以传 `mode="vector"` 跳过词法检索。
- 进程内存在 1 千条时约 790 MB，主要是 Python 与依赖库本身；10 万条时约 1.9 GB。

数据来自 LongMemEval-S 的真实对话轮次，测试机为普通 Windows 桌面 CPU、Python 3.14。完整报告：[v2.4](https://github.com/snowfoxHQ/vecRecall/blob/main/benchmarks/results/scale_bow_v24.md)、[v2.3](https://github.com/snowfoxHQ/vecRecall/blob/main/benchmarks/results/scale_bow.md)。复现：`python benchmarks/bench_scale.py --data benchmarks/data/longmemeval_s_cleaned.json`。

---

## 功能一览

| 功能 | 说明 | 文档 |
|---|---|---|
| 认知层 | 写入时自动抽取话题、重要性、摘要、实体与关系；启发式零依赖，或接 Ollama / OpenAI 兼容 LLM | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#认知层llm-抽取--语义去重) |
| 矛盾检测 | 新记忆推翻旧记忆时（「主库从 PostgreSQL 换成 MySQL」），旧记忆标记为被取代，检索时降权，不删除；可接 LLM 判断 | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#矛盾检测) |
| 来源追踪 | 每条记忆记下写入入口（python / mcp / rest / cli）、Agent、会话、轮次和时间；重复写入、取代、蒸馏关系都能查到 | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#来源追踪) |
| 语义去重 | 与已有记忆高度相似时合并到已有记忆，计数加一 | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#语义去重合并) |
| 时序知识图谱 | 记录实体、关系和属性随时间的变化（「谁在什么时候是什么状态」） | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#时序知识图谱) |
| 多跳检索 | 向量命中后沿实体关系扩展，返回带路径的证据 | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#多跳向量图谱混合检索) |
| 遗忘曲线与蒸馏 | 记忆强度随时间衰减；碎片记忆可压缩成一条长期记忆 | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#遗忘曲线--主动蒸馏) |
| 类型化记忆 | episodic / semantic / procedural；procedural 按场景标签 + 版本精确匹配 | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#类型化记忆与多-agent-隔离) |
| 多 Agent 隔离 | `owner` × `visibility`（private / shared）；private 只有写入者能看到 | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#类型化记忆与多-agent-隔离) |
| 不可变记忆与哈希链 | `retention_policy="immutable"` 不去重、不遗忘、不被清理；哈希链按 `topic_key` 分链校验 | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#类型化记忆与多-agent-隔离)、[区块链存档](https://github.com/snowfoxHQ/vecRecall/blob/main/README_BLOCKCHAIN.md) |
| 静态加密 | 记忆原文、摘要、元数据加密落盘 | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#静态加密at-rest-encryption) |
| 写入治理 | 写入前脱敏、幂等写回队列、检索决策、仓库知识映射 | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#写入治理检索决策与仓库知识映射) |
| 推理链与反思 | 记录观察 / 假设 / 决策 / 行动，事后用结果评分，把有效决策写回长期记忆 | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#推理链与反思) |
| 扩展钩子 | `before_write` / `after_write` / `recall_filter` / `on_delete`，插件不用改核心 | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#扩展钩子) |
| 插件 | Trace 本地事件日志；记忆卡片门槛（未验证的经验不能以「已验证」入库） | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#插件) |
| 后端可插拔 | 向量：SQLite（默认）/ Chroma / 内存；嵌入：词法 / sentence-transformers / Ollama / OpenAI 兼容 API | [指南](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#后端可插拔) |

---

## 接入方式

| 方式 | 入口 | 说明 |
|---|---|---|
| Python | `from vecrecall import VecRecall` | 全部功能 |
| MCP | `vr-mcp` / `vr mcp` | 41 个工具，适用于 Claude Code、Gemini CLI 等 MCP 客户端 |
| REST | `vr-api` / `vr api` | 零依赖 HTTP JSON 接口，默认 `127.0.0.1:8791` |
| CLI | `vr` | 34 个子命令 |

完整的工具列表、端点、命令和三个入口的能力对照表见 [docs/interfaces.md](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/interfaces.md)。

---

## 安全与隐私

- 全部本地运行，核心功能不需要任何 API Key；数据默认在 `~/.vecrecall`。
- **默认不加密**。存放敏感内容时请开启静态加密（`vr init --encrypt`），或开启写入前脱敏（`redact_payloads=True`）；也附带只读审计工具，可扫描已入库记忆中是否混入密钥。
- 静态加密覆盖记忆主库；实际加密覆盖范围可用 `vr encryption status` 查看。
- REST：设置 `--api-key` 后必须带 Bearer token；监听非本机地址时强制要求 token；默认不发 CORS 头。
- MCP 的导入导出路径限制在数据目录内；数据访问全程使用参数化查询。

---

## 开发

```bash
pip install -e ".[crypto,dev]"
python -m pytest
```

持续集成在多个平台与 Python 版本上运行全部测试并做打包检查；发版由版本标签触发，自动构建并发布到 PyPI。

---

## 版本记录

每个版本的改动见 [CHANGELOG.md](https://github.com/snowfoxHQ/vecRecall/blob/main/CHANGELOG.md)，早期版本的详细升级说明见 [docs/upgrade-notes.md](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/upgrade-notes.md)。

## 许可证

MIT
