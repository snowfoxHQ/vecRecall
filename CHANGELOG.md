# 更新日志

最新版本排在最前。详细设计说明见 [README.md](README.md) 的各版本升级说明章节。

## 未发布（v2.3 开发中）

- **会话存档默认改为逐轮存储**：新增 `VecRecall.archive_session(messages, granularity="turn")`，每轮对话一条记忆，共用同一个 `session_id`，metadata 记录 `turn_index` 与 `role`；新增 `session_memories(session_id)` 按顺序取回整段会话。MCP `mp_archive_session` 与 CLI `vr archive` 改为调用它。依据：LongMemEval-S（词法嵌入，470 题），相比整段存一条，会话级 R_any@5 75.3% → 92.1%、R_any@1 50.0% → 77.9%，top-5 上下文约 13,900 → 900 token；代价是记忆条数约增加 10 倍，SQLite 后端检索延迟 p50 从约 2 ms 升到约 18 ms。**行为变化**：需要旧行为请传 `granularity="session"` 或 `vr archive --granularity session`。逐轮模式默认关闭语义去重，避免不同会话里的相同语句被合并。`mp_archive_session` 返回值新增 `session_id` / `granularity` / `archived` / `ids`，保留 `archived_id`（逐轮模式下为第一条）。`vr archive` 读取文件改为显式 UTF-8，并新增 `--session-id`。
- **BM25 + RRF 混合检索**：新增 `core/lexical.py`（零依赖 BM25 倒排索引 + Reciprocal Rank Fusion）。`search()` / `build_context()` 新增 `mode` 参数：`vector`（默认，行为不变）/ `bm25`（纯词法）/ `rrf`（向量与 BM25 各取 n×3 条候选，按名次融合，k=60）。词法索引在首次 bm25 / rrf 检索时从 SQLite 原文构建，之后随 add / update / delete / distill 增量维护，`reindex()` 后重建；可见性过滤与 `recall_filter` 钩子同样生效。MCP `mp_search`、REST `/search`、CLI `vr search --mode` 同步支持。LongMemEval 评测新增 `bm25` / `rrf` 配置。已知限制：多进程同时写同一数据目录时，其他进程新写入的记忆要等本进程重建索引后才能被词法检索到。
- **hybrid 时间基准**：`search()` / `build_context()` 新增可选参数 `as_of`，按指定时刻计算时间新鲜度；不传时与之前一致。LongMemEval 中 hybrid 以提问时刻为基准后，knowledge-update +4.2、temporal-reasoning -3.1，整体 R_any@5 74.7%（vector 75.3%），hybrid 仍默认关闭。
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
