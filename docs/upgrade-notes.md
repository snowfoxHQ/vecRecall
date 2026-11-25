# 早期版本升级说明

这些说明原先在 README 末尾，v2.3 起移到这里。每个版本的改动摘要见 [CHANGELOG.md](../CHANGELOG.md)。

## v2.2 升级说明（运行时原语）

v2.2 把类型化、隔离、不可变与分链能力并入公开核心。所有新参数都有默认值，老代码无需改动，老库打开时自动 migration。

| 升级项 | 说明 |
|---|---|
| **类型化记忆** | 新增 `memory_type`（episodic / semantic / procedural），与 L0–L3 加载策略正交。`semantic` 必进时序图谱（无 extractor 时启发式兜底）；`procedural` 用 `query_procedural(scenario_tags, version)` 精确匹配 |
| **类型过滤** | `build_context(memory_types=[...])` 一处过滤，L1/L2/L3 同时生效 |
| **隔离** | 新增 `visibility`（private/shared）与 `owner`，与 wing 组成三元组；`update_visibility()` 原子操作 |
| **不可变记忆** | 新增 `retention_policy`（decay 默认 / immutable）。immutable 不去重、不遗忘、不 prune、不蒸馏、不脱敏，始终进入 L1；`update_retention_policy()` 原子操作 |
| **哈希链分链** | `blocks` 表新增 `topic_key`，按 `(wing, topic_key)` 建链与验证；默认链（`topic_key=""`）向后兼容 |
| **按主题查询** | `query_by_topic_key(prefix)` + `verify_integrity(topic_key=...)`，可单独验证一条主题链 |
| **跨 wing 关联收紧** | `mp_link` 跨 wing 仅对双方 shared 的记忆开放 |
| **核心边界** | 核心只接受 `memory_type` / `visibility` / `owner` / `retention_policy` / `topic_key` 等通用原语，任何场景 schema 不进核心，由源码断言测试守护 |
| **代码拆分** | `core/engine.py` 拆出 `models.py` / `vectors.py` / `embeddings.py` / `storage.py`；`from vecrecall.core.engine import ...` 写法保持可用 |
| **扩展钩子** | 新增 `core/hooks.py`：`before_write` / `after_write` / `recall_filter` / `on_delete`，见上文「扩展钩子」 |
| **统一凭据规则** | `guard.py` 委托 `redaction.py`，两套规则合一；补充句中赋值等识别 |
| **修复：检索丢弃负分结果** | `search()` 与 L3 默认 `min_score=None`，始终返回 top-n。**行为变化**：需要按分数截断请显式传 `min_score=0.0` |
| **公开评测** | 新增 `benchmarks/longmemeval.py`，LongMemEval-S 词法嵌入基线 R_any@5 75.3% |

> 已知边界：immutable 记忆只增不减，长期运行需要配套冷存储归档（未实现）。

---

## v2.1 升级说明（治理层）

v2.1 不改变 v2.0 的认知层与检索语义，只补治理层：写入前脱敏（`redact_payloads`，默认关闭且不可逆）、写回治理（幂等/缓冲/重试 + `writeback_outbox`）、检索决策（三项验收，只附加不改排序）、仓库知识映射（`vr repo-map`，不进向量索引）。新增 CLI 子命令 `writeback` / `recall-policy` / `repo-map`，MCP 工具从 32 增至 34。版本号在 `pyproject.toml`、`vecrecall/__init__.py`、`api/server.py`、`mcp/server.py` 四处统一为 `2.1.0`。

---

## v2.0 升级说明

v2.0 补齐「认知层」，从被动 RAG 记忆库升级为主动结构化记忆系统：

| 升级项 | 说明 |
|---|---|
| **LLM 记忆抽取器** | 新增 `MemoryExtractor` 抽象 + `HeuristicExtractor`（零依赖）/ `LLMExtractor`（Ollama / OpenAI 兼容）两种实现，写入时自动抽取话题、重要性、摘要、实体、关系、实体状态，失败自动降级 |
| **语义去重合并** | `add()` 默认开启去重：相似度 ≥ 0.92 时合并到既有记忆（`occurrences+1`），避免重复膨胀，`dedup=False` 可关闭 |
| **时序知识图谱** | 新增 `TemporalGraph`（`graph.db`）：实体节点 + 状态时间线 + 带时间戳关系，`entity()` / `entity_timeline()` / `entity_relations()` / `list_entities()` 可追溯「谁在什么时候是什么状态」 |
| **遗忘曲线 & 蒸馏** | 新增 `ForgettingCurve`（艾宾浩斯指数衰减，重要性 + 复习间隔效应修正）：`memory_strength()` / `forgotten()` / `recall()`；`distill()` 主动蒸馏把碎片化记忆压缩为抽象长期记忆；`prune(use_forgetting=True)` 按遗忘曲线清理 |
| **多跳混合检索** | 新增 `graph_search()`：向量召回种子 → 沿实体关系图 BFS 扩展关联实体 → 返回带证据路径的关联记忆，`TemporalGraph` 增加实体-记忆映射与 `neighbors()` 反查 |
| **接口安全防护** | REST 增加 Bearer Token 认证（`--api-key` / `VR_API_KEY`）+ CORS 收紧（默认禁止跨域）；MCP 文件路径隔离防任意读写；SQL 参数化加固 |
| **静态加密（at-rest）** | 新增 `vecrecall/core/crypto.py`（`Cipher`，Fernet 认证加密 + PBKDF2 密码派生）：`encryption_key` 参数加密 `knowledge.db` 的记忆内容/摘要/元数据落盘，错误密钥拒绝读取，兼容明文旧数据；`vr init --encrypt` / `VR_ENCRYPTION_KEY` 启用 |
| **语义嵌入升级** | 词法嵌入增加 subword 增强（英文字符 3/4-gram + 中文三元组），捕获词形/拼写变体；`sentence` / `auto` 默认模型升级为多语言 `paraphrase-multilingual-MiniLM-L12-v2`；新增 `benchmarks/benchmark_recall.py` 可复现召回率评测（词法 76.2% / 语义 100%） |

新增 CLI 参数：`--extractor` / `--llm-model` / `--llm-url` / `--llm-api-key`（`vr init` 持久化，`vr add` / `vr mcp` / `vr api` 可临时覆盖），`--encrypt` / `--encrypt-key`（静态加密）。新增查询命令：`vr graph`（列出实体）/ `vr entity NAME`（实体画像与状态演化）/ `vr decay`（遗忘评估）/ `vr distill`（主动蒸馏）/ `vr graph-search QUERY`（多跳混合检索）。

---

## v1.1 升级说明

基于架构缺口分析完成的升级（相对 v1.0.x）：

| 升级项 | 说明 |
|--|--|
| **持久化向量后端** | 原默认 `NumpyVectorBackend` 纯内存，重启后检索全部失效；新增 `SqliteVectorBackend` 作为零依赖默认后端，向量以二进制 BLOB 存 SQLite |
| **词法语义嵌入** | 原 `HashEmbeddingBackend`（整段文本一个哈希，无语义）替换为 `BagOfWordsEmbeddingBackend`（特征哈希 + 中文单字/二元组），默认安装即获得词面级真实语义 |
| **记忆生命周期** | 新增 `update` / `delete` / `prune` / `reindex`，向量索引与 SQLite 始终同步，补齐"只能增不能改删"的缺口 |
| **混合评分检索** | `search`/`build_context` 支持 `hybrid=True`，分数 = 0.6·相似度 + 0.25·重要性 + 0.15·时间新鲜度（opt-in，不改变纯向量召回集合） |
| **区块链集成** | 原先独立的 `blockchain/` 模块接入核心引擎：`use_blockchain=True` 时写入记忆同步生成防篡改区块，`verify_integrity()` 检测原文篡改 |
| **中文 token 估算** | `estimate_tokens()` 区分中英文（中文 ≈ 0.7 token/字，其他 ≈ 4 字符/token） |
| **嵌入后端双选择** | 新增 `OllamaEmbeddingBackend`（本地 Ollama，零 Key）与 `APIEmbeddingBackend`（OpenAI 兼容远程），配 `build_embedding_backend()` 工厂，CLI/MCP/REST 均可 `--embedder` 选择 |
| **HTTP REST API** | 新增 `vr api` / `vr-api`：零依赖 `http.server` 实现的 JSON 接口，供传统软件（curl/任意语言）接入，带 CORS |
| **回吐记忆列表** | 新增 `VecRecall.list_memories()` + MCP `mp_list_memories` + REST `GET /memories` + CLI `vr list`：分页/过滤/排序/可选原文的结构化记忆清单 |

新增 CLI 子命令：`vr delete`、`vr prune`、`vr verify`、`vr reindex`、`vr list`、`vr api`；新增 MCP 工具：`mp_update`、`mp_delete`、`mp_prune`、`mp_reindex`、`mp_verify`、`mp_list_memories`。
