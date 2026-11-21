# 更新日志

最新版本排在最前。详细设计说明见 [README.md](README.md) 的各版本升级说明章节。

## v2.1.0（2026-11-21）治理层

在不改变 v2.0 认知层与检索语义的前提下补齐治理机制，默认行为不变：

- **写入前脱敏**：`redact_memory_payload()` + `VecRecall(redact_payloads=True)`，8 类敏感信息规则表、白名单、截断边界防半截密钥。默认关闭、不可逆，与静态加密互补。
- **写回治理**：`WritebackGovernor`（幂等键 + turn/字符/消息/idle 四维缓冲 + 重试）与 `SqliteOutbox`（`writeback_outbox` 表）；新增 `vr writeback stats|recover`。
- **检索决策**：`decide_recall()` / `build_query_intent()` / `evaluate_memory()` 三项验收；MCP 新增 `mp_recall_policy`、`mp_accept_memory`，`mp_search.acceptance_query` 只附加验收、不改变召回与排序。
- **仓库知识映射**：`vr repo-map build|update|validate`，产物在 `.vecrecall/repo/`，按 baseline SHA 增量更新并校验；不进入向量索引。
- 新增 CLI 子命令 3 个，MCP 工具 32 → 34。回归：158 + 72 + 34 + 19 + 14 项断言全通过。

> 注：本仓库是 v2.0 认知层 + 治理层的延续；类型化 / 隔离 / 审计豁免 / 分链 / 契约适配层位于 [snowfoxHQ/vecrecall-runtime](https://github.com/snowfoxHQ/vecrecall-runtime) 的 v2.1/v2.2。

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
