# 更新日志

最新版本排在最前。各功能的用法见 [docs/guide.md](docs/guide.md)，v2.2 及更早版本的详细升级说明见 [docs/upgrade-notes.md](docs/upgrade-notes.md)。

## v2.4.0（2026-11-25）检索性能

10 万条记忆时，默认 `rrf` 检索 4,907 → 45 ms，开启去重的写入 4,324 → 16 ms，新进程首次检索 14 秒以上 → 5.6 秒。召回结果与 v2.3 一致。

### 升级须知

1. **默认向量后端改为内存索引 + 可选 numpy 矩阵计算**。`SqliteVectorBackend` 首次检索时把向量载入内存（装了 numpy 时整表一次载入），之后随写入 / 删除增量维护；其他进程写同一个库时通过 `PRAGMA data_version` 发现并重新载入。10 万条时 `vector` 检索 4,631 → 11 ms，`rrf` 4,907 → 45 ms，默认去重写入 4,324 → 16 ms。新增安装选项 `vecrecall[fast]`（numpy），`[full]` 也包含；没装 numpy 时退回纯 Python。设置 `VECRECALL_NO_NUMPY=1` 可强制纯 Python。
2. **新写入的向量存为 float32**（原为 float64）。旧库无需迁移，两种格式可混存；但 v2.3 及更早版本读不了 v2.4 写入的向量，降级前请先 `reindex`。
3. **新增词频缓存文件 `<base_dir>/lexical.db`**（约 0.5 KB/条），新进程不必重新分词；内容等同于原文，开启静态加密的库不会生成它。可设 `palace.lexical_cache = False` 关闭，删除该文件也安全（下次检索时重建）。
4. 装了 numpy 时分数按 float32 计算，与 v2.3 在小数点后第 6–7 位可能不同；分数恰好等于阈值的条目可能落到另一侧。

### 改动明细

- **向量化检索**：新增 `core/dense.py`（`DenseIndex`），`NumpyVectorBackend` / `SqliteVectorBackend` 共用；结果顺序与 v2.3 一致（分数降序，同分按写入顺序）。新增 `tests/test_dense.py`，以 v2.3 逐条计算为对照；CI 分别在纯 Python 与 numpy 下运行。
- **整表载入向量**：装了 numpy 时整表一次解码、归一化，10 万条载入从约 1.5 秒降到约 0.2 秒。
- **BM25 词频缓存**：新增 `LexicalCache`，按原文与分词版本的签名逐条校验，签名不符或缺失才重新分词，已删除的记忆自动清理；构建统计见 `palace.last_lexical_build`。构建只读 id 与原文两列。10 万条时新进程首次 rrf 检索 9.3 → 5.6 秒，同进程首次 14.2 → 8.6 秒。
- **性能评估**：`bench_scale.py` 新增「新进程首次检索」测量；结果见 `benchmarks/results/scale_bow_v24.md`。

## v2.3.0（2026-11-25）召回质量与 HMR 模块移植

LongMemEval-S（470 题）会话级 R_any@5：零依赖默认配置 75.3% → **94.7%**；装语义模型后 **98.3%**。每次召回放进 prompt 的上下文（top-5）从约 13,900 token 降到约 1,100 token。

### 升级须知（行为变化）

1. **默认检索模式改为 `rrf`**。`search()` / `build_context()` / `mp_search` / REST `/search` / `vr search` 不传 `mode` 时走 rrf，返回的 `score` 是名次融合分数（约 0.01–0.04），**不再是余弦相似度**。按分数阈值过滤的调用方请显式传 `mode="vector"`，或设置 `palace.DEFAULT_SEARCH_MODE = "vector"`。L2 语义触发（0.55）与写入去重（0.92）仍按余弦相似度判断。
2. **会话存档默认逐轮存储**。`mp_archive_session` / `vr archive` 每轮对话写一条记忆，共用 `session_id`，记忆条数约为原来的 10 倍。需要旧行为请传 `granularity="session"` 或 `--granularity session`。`mp_archive_session` 返回值新增字段，原有 `archived_id` 保留（逐轮模式下为第一条）。
3. **检索会多走一路 BM25**。词法索引在首次 rrf / bm25 检索时从 SQLite 原文构建，之后增量维护；SQLite 后端在约 500 条记忆时检索 p50 约 56 ms（rrf）对 15 ms（vector）。
4. v2.2 的数据库可以直接打开，不需要迁移；之前按整段会话存的记忆照常可以检索。
5. **已知限制：默认 SQLite 向量后端是全量扫描**。`vector` / `rrf` 检索与开启去重的 `add()` 约 0.045 ms/条：1 万条约 0.45 秒，10 万条约 4.6–4.9 秒；BM25 在 10 万条时仍为 30 ms。记忆较多时请用 `archive_session()` / `dedup=False` 写入，或换 Chroma 后端。ANN 计划在 v2.4。

### 改动明细

- **会话存档默认改为逐轮存储**：新增 `VecRecall.archive_session(messages, granularity="turn")`，每轮对话一条记忆，共用同一个 `session_id`，metadata 记录 `turn_index` 与 `role`；新增 `session_memories(session_id)` 按顺序取回整段会话。MCP `mp_archive_session` 与 CLI `vr archive` 改为调用它。依据：LongMemEval-S（词法嵌入，470 题），相比整段存一条，会话级 R_any@5 75.3% → 92.1%、R_any@1 50.0% → 77.9%，top-5 上下文约 13,900 → 900 token；代价是记忆条数约增加 10 倍，SQLite 后端检索延迟 p50 从约 2 ms 升到约 18 ms。**行为变化**：需要旧行为请传 `granularity="session"` 或 `vr archive --granularity session`。逐轮模式默认关闭语义去重，避免不同会话里的相同语句被合并。`mp_archive_session` 返回值新增 `session_id` / `granularity` / `archived` / `ids`，保留 `archived_id`（逐轮模式下为第一条）。`vr archive` 读取文件改为显式 UTF-8，并新增 `--session-id`。
- **记忆卡片门槛（移植自 HMR memory_card）**：新增插件 `vecrecall.plugins.memory_card`，`enable_memory_cards(palace)` 后，以 `CODE_AGENT_MEMORY` 开头的结构化经验卡片在写入前（`before_write`，覆盖 Python API / MCP / REST / CLI 所有入口）按门槛校验：`status=verified` 必须写 `validation` 且 `signal` 不能为空或 unknown，`failed_attempt` 必须写 `failed_shape`，`problem` 不能为空。默认严格模式直接拒绝；`strict=False` 时把不合格的 verified / failed_attempt 降级为 candidate。卡片字段写入 metadata，调用方无法通过 metadata 覆盖状态；卡片不参与语义去重；`list_cards()` 按状态、仓库列出。核心新增 metadata 标记 `_no_dedup`，钩子可借此让单条写入跳过去重（标记不落盘）。
- **推理链与反思（移植自 HMR ThoughtChain）**：新增可选模块 `vecrecall.reasoning`。`ReasoningEngine(palace)` 记录一次推理任务的观察 / 假设 / 决策 / 行动，`reflect()` 用实际结果评分并标记每个节点对错；评分明确（> 0.85 或 < 0.25）时把结论作为 semantic 记忆写入主库（topic=`reasoning_insight`），写入失败时留在链上，可用 `retry_pending_insights()` 补写。`find_similar()` 用 rrf 检索取候选、再按词重叠过滤；`best_decision_for()` 返回相似历史链中准确率最高的正确决策。链保存在独立推理库 `<base_dir>/reasoning/`，推理过程不会出现在主库检索结果中；遵循 owner / visibility 隔离，主库加密时同样加密。不依赖 pydantic / numpy。
- **Trace 插件（移植自 HMR）**：新增 `vecrecall.plugins.trace`，通过扩展钩子（`after_write` / `on_delete` / `recall_filter`）把写入、去重合并、删除、召回事件记成按天分文件的 JSONL（默认 `<base_dir>/traces/`）。`enable_trace(palace)` 一行开启，默认只记元数据，`capture_content=True` 时记录原文并先脱敏再截断；脱敏复用 `core/redaction.py`。event_id 进程内去重、保留期按文件名日期清理（默认 7 天）、单文件大小上限后分文件。Trace 写入失败只计数，不影响记忆写入与检索。已知限制：零结果的检索不会留下记录；召回事件不含查询原文。
- **默认检索模式改为 rrf**：`search()` / `build_context()` 不传 `mode` 时使用 `VecRecall.DEFAULT_SEARCH_MODE`（默认 `"rrf"`），MCP `mp_search`、REST `/search`、CLI `vr search` 同步。依据：LongMemEval-S（逐轮存储、词法嵌入、470 题）会话级 R_any@5 vector 92.1% → rrf 94.7%、R_any@1 77.9% → 87.0%；内置评测 R@1 52.4% → 61.9%、近义改写组 R@5 16.7% → 50.0%。纯 bm25 在 LongMemEval 上略高（96.6%），但无法利用语义嵌入，因此默认选 rrf。**行为变化**：① rrf 下 `score` 是名次融合分数（约 0.01–0.04），不再是余弦相似度，依赖分数阈值的调用方请显式传 `mode="vector"`；② 检索要多走一路 BM25，词法索引首次检索时构建；③ 需要旧行为可传 `mode="vector"`，或设置 `palace.DEFAULT_SEARCH_MODE = "vector"`。L2 语义触发与去重仍按余弦相似度，不受影响。
- **BM25 + RRF 混合检索**：新增 `core/lexical.py`（零依赖 BM25 倒排索引 + Reciprocal Rank Fusion）。`search()` / `build_context()` 新增 `mode` 参数：`vector`（纯向量）/ `bm25`（纯词法）/ `rrf`（向量与 BM25 各取 n×3 条候选，按名次融合，k=60）。词法索引在首次 bm25 / rrf 检索时从 SQLite 原文构建，之后随 add / update / delete / distill 增量维护，`reindex()` 后重建；可见性过滤与 `recall_filter` 钩子同样生效。MCP `mp_search`、REST `/search`、CLI `vr search --mode` 同步支持。LongMemEval 评测新增 `bm25` / `rrf` 配置。已知限制：多进程同时写同一数据目录时，其他进程新写入的记忆要等本进程重建索引后才能被词法检索到。
- **hybrid 时间基准**：`search()` / `build_context()` 新增可选参数 `as_of`，按指定时刻计算时间新鲜度；不传时与之前一致。LongMemEval 中 hybrid 以提问时刻为基准后，knowledge-update +4.2、temporal-reasoning -3.1，整体 R_any@5 74.7%（vector 75.3%），hybrid 仍默认关闭。
- **检索性能评估**：新增 `benchmarks/bench_scale.py`，用 LongMemEval 真实对话轮次从 1 千写到 10 万条，测三种检索模式、首次建索引、去重写入、磁盘与内存。结果见 `benchmarks/results/scale_bow.md`。
- **语义嵌入基线**：LongMemEval-S（逐轮存储、默认多语言 MiniLM、470 题）rrf R_any@5 98.3%、R_any@1 91.3%、MRR 0.944，各项均高于纯 bm25（96.6% / 87.9% / 0.914）；偏好类 R_any@5 96.7%。评测脚本新增按题批量嵌入缓存，各配置共用同一份向量。
- **README 重写**：按当前功能重新组织，详细用法移到 `docs/guide.md`，接口清单移到 `docs/interfaces.md`，早期升级说明移到 `docs/upgrade-notes.md`；删除无法复现的原版召回率对比数字。
- **CI**：新增 GitHub Actions，测试矩阵 Linux Python 3.10–3.12 + Windows 3.12，含打包检查；新增基于 Trusted Publishing 的标签自动发布工作流与 `RELEASING.md`。
- CLI 描述更新为新的定位。

## v2.2.0（2026-11-25）运行时原语并入核心

把类型化、隔离、不可变与分链能力并入公开核心。新参数全部有默认值，老代码无需改动，老库打开时自动 migration。

- **类型化记忆**：`memory_type`（episodic / semantic / procedural）；`semantic` 必进时序图谱；`procedural` 用 `query_procedural(scenario_tags, version)` 精确匹配。
- **类型过滤**：`build_context(memory_types=[...])` 一处过滤 L1/L2/L3。
- **隔离**：`visibility`（private/shared）+ `owner`，与 wing 组成三元组；`update_visibility()` 原子操作。
- **不可变记忆**：`retention_policy`（decay / immutable）；immutable 不去重、不遗忘、不 prune、不蒸馏、不脱敏，始终进入 L1；`update_retention_policy()` 原子操作。
- **哈希链分链**：`blocks` 表新增 `topic_key`，按 `(wing, topic_key)` 建链与验证；老库自动 migration。
- **按主题查询**：`query_by_topic_key(prefix)` + `verify_integrity(topic_key=...)`。
- **跨 wing 关联收紧**：`mp_link` 仅对双方 shared 的记忆开放。
- **核心边界守护**：新增源码断言测试，核心不得出现场景 schema（如 `contract_id`），也不得引用 `vecrecall.adapters`。场景适配器不随核心发布。
- **代码拆分**：`core/engine.py` 拆出 `models.py` / `vectors.py` / `embeddings.py` / `storage.py`，原有 `from vecrecall.core.engine import ...` 写法保持可用。
- **扩展钩子**：新增 `core/hooks.py`，提供 `before_write` / `after_write` / `recall_filter` / `on_delete` 四个调用点，支持插件对象与 `vecrecall.plugins` entry point（`VecRecall(load_plugins=True)`）。默认不注册任何钩子，行为不变。
- **统一凭据规则**：`core/guard.py` 改为委托 `core/redaction.py`，两套规则合并为一套；补充句中赋值（如「数据库 password=[REDACTED_PASSWORD] 别外传」）、较短的 npm token、宽松 JWT 的识别。`find_secrets()` 返回的类型名改为英文（如 `CREDENTIAL`）。
- **修复：检索丢弃负分结果**。余弦相似度可以为负（词法嵌入在没有共同词时尤其常见），旧版 `search()` 默认 `min_score=0.0`，会把排在后面的真正答案直接丢掉，出现「库里只有 4 条、取 top-5 却返回不到答案」。现在 `search()` 与 L3 检索默认 `min_score=None`，始终返回 top-n；MCP `mp_search` 与 REST `/search` 同步去掉 0.0 默认值。**行为变化**：需要按分数截断的调用方请显式传 `min_score=0.0`。L2 触发阈值（0.55）、去重阈值（0.92）、`graph_search` 默认 0.1 不变。
- **评测**：新增 `benchmarks/longmemeval.py`（LongMemEval 会话级 / 轮次级检索评测，vector / hybrid / dedup / graph 四种配置对比）与共用指标 `benchmarks/_metrics.py`；内置评测 `benchmark_recall.py` 增加 R@1、R@10、nDCG@10 与 hybrid 对比。
- **文档**：更正 README 中「记忆原文以明文存储（无 at-rest 加密）」的过时说明；测试章节列出全部测试文件。
- 版本号在 `pyproject.toml`、`vecrecall/__init__.py`、`api/server.py`、`mcp/server.py` 统一为 `2.2.0`。MCP 工具数保持 34。

## v2.1.0（2026-11-21）治理层

在不改变 v2.0 认知层与检索语义的前提下补齐治理机制，默认行为不变：

- **写入前脱敏**：`redact_memory_payload()` + `VecRecall(redact_payloads=True)`，8 类敏感信息规则表、白名单、截断边界防半截密钥。默认关闭、不可逆，与静态加密互补。
- **写回治理**：`WritebackGovernor`（幂等键 + turn/字符/消息/idle 四维缓冲 + 重试）与 `SqliteOutbox`（`writeback_outbox` 表）；新增 `vr writeback stats|recover`。
- **检索决策**：`decide_recall()` / `build_query_intent()` / `evaluate_memory()` 三项验收；MCP 新增 `mp_recall_policy`、`mp_accept_memory`，`mp_search.acceptance_query` 只附加验收、不改变召回与排序。
- **仓库知识映射**：`vr repo-map build|update|validate`，产物在 `.vecrecall/repo/`，按 baseline SHA 增量更新并校验；不进入向量索引。
- 新增 CLI 子命令 3 个，MCP 工具 32 → 34。回归：158 + 72 + 34 + 19 + 14 项断言全通过。


## v2.0.0 认知层

基于对原版 MemPalace 的设计分析重新构建：

- **LLM 认知层**：写入时自动抽取实体 / 关系 / 摘要 / 话题，语义去重合并（`dedup_threshold=0.92`）。
- **时序知识图谱**：实体状态随记忆演进，支持多跳混合检索。
- **遗忘曲线与主动蒸馏**：艾宾浩斯遗忘 + 关键片段蒸馏。
- **检索与组织解耦**：检索走纯向量，组织层（wing/topic）只影响 UI，不过滤召回。
- **四层上下文栈**（L0–L3）：身份 / 关键片段 / 主题上下文 / 深度召回。
- **语义嵌入升级**：subword 增强词法（零依赖）+ 多语言语义模型（可插拔）。
- **静态加密**：`encryption_key` 可选，SQLite 主库静态加密。
- MCP stdio 与 REST 双接口，CLI 工具链；凭据护栏 `vecrecall/core/guard.py` + 只读审计 `scripts/audit_secrets.py`。
