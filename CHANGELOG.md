# 更新日志

最新版本排在最前。详细设计说明见 [README.md](README.md) 的各版本升级说明章节。

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
