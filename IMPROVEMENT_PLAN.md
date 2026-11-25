# VecRecall 全面评估与改进计划

**评估日期：2026-10-16**  
**评估对象：** 当前工作区中的 VecRecall v2.0 源码、README、内置 benchmark 与测试代码。  
**评估方式：** 静态代码审阅、功能面梳理、对照项目内文档和截至评估日公开的同类产品文档。没有运行完整测试套件，也没有用生产级数据重新测量性能；本计划中的验收指标是建议目标，不是当前成绩。

## 1. 执行摘要

VecRecall 已经形成一个边界清晰的本地优先原型：Python API、CLI、MCP stdio 和 REST API 共用同一引擎，支持 SQLite 持久化、可插拔嵌入、记忆抽取、语义去重、时间图谱、上下文构建、归档、导入导出和可选加密。最值得保留的产品方向是**用户掌控数据、本地可运行、接入路径轻、原文可追溯**。

目前主要短板不在“缺少更多记忆概念”，而在**隔离语义、生命周期一致性、隐私边界、检索质量证据和生产可运维性**。README 描述的功能面较宽，但部分能力只是基础实现：图谱不是完整的事实失效/时态查询系统，遗忘曲线不是默认召回策略，哈希链不是跨库事务审计，加密也并未覆盖所有本地数据库。

**建议定位：** 保持个人开发者和小团队可自托管的 agent memory toolkit；先把本地数据正确性与隔离做扎实，再把检索做成可测量、可解释、可配置，最后拓展多租户部署和集成生态。不要在隔离、恢复和评测仍不完整时宣称“生产级”或使用未经可比验证的市场优越性话术。

### 实施状态更新（2026-11-25，v2.2）

v2.2 把类型化、隔离、不可变与分链能力并入公开核心，并补上公开评测与扩展钩子。对本计划各项的影响：

| 本计划条目 | 状态 | 处理方式 |
|---|---|---|
| 发现 2：wing 不是隔离边界，需 namespace / scoped 检索 | **基本解决** | `visibility`（private/shared）+ `owner` 双层隔离，L1/L2/L3、图谱扩展、procedural 查询统一按可见性过滤；跨 wing 关联只对 shared 开放；另提供 `recall_filter` 钩子供外部做授权。**残留**：去重候选池仍跨 owner |
| 记忆类型（semantic / episodic / procedural） | **已解决** | `memory_type` 落地；semantic 必进图谱；procedural 按场景标签 + 版本精确匹配；`build_context(memory_types=...)` 类型过滤 |
| 发现 8：遗忘曲线不影响默认检索 | **部分解决** | `retention_policy=immutable` 的记忆不遗忘、不 prune，始终进 L1；常规记忆的强度仍不参与默认排序 |
| 哈希链 → 审计 | **部分解决** | 按 `(wing, topic_key)` 分链，单主题证据可独立验证导出；仍不是跨库事务 |
| 发现 15：质量体系（基准规模小、无 CI、测试非 pytest） | **部分解决** | 基准部分：新增 LongMemEval 检索评测（`benchmarks/longmemeval.py`），指标含 Recall / nDCG / MRR / 延迟。词法嵌入全量基线：会话级 R_any@5 75.3%、R_any@10 84.9%、R_all@10 61.7%，详见 `benchmarks/results/`。**仍缺**：CI、pytest 化、置信区间 |
| 发现 16：公开文档承诺需校准 | **已解决** | README 召回率改用 LongMemEval 数字，内置评测标注为冒烟测试 |
| 可扩展性 | **新增能力** | `core/hooks.py`：`before_write` / `after_write` / `recall_filter` / `on_delete`，支持 entry point 插件 |
| 代码结构 | **改善** | `engine.py` 拆为 models / vectors / embeddings / storage / engine，旧导入保持兼容 |

**评测发现的新问题：**

1. **检索丢弃负分结果（已修复）**：余弦相似度可以为负，旧版 `search()` 默认按 0 截断，会把排在后面的真正答案丢掉。现在默认始终返回 top-n。
2. **hybrid 的时间新鲜度按当前时间计算（未处理）**：记忆都较旧时（LongMemEval 的会话在 2023 年），新鲜度全部接近 0，hybrid 与纯向量排名完全相同。应改为以查询时刻为基准。
3. **召回上下文过大（未处理）**：整段会话存为一条记忆时，top-5 结果约 13,900 token，远超四层栈「每次几百 token」的设计。需要更细的写入粒度或会话内片段检索。
4. **偏好类题目召回低（未处理）**：LongMemEval 的 single-session-preference 只有 26.7%，词法嵌入无法匹配语义相近、用词不同的表述，待语义嵌入与混合检索验证。

**发现 6 已量化（2026-11-25）**：默认 SQLite 向量后端检索与开启去重的写入约 0.045 ms/条，1 万条 0.45 秒、10 万条 4.6–4.9 秒（`benchmarks/results/scale_bow.md`）。v2.4 优先处理：向量化计算或 ANN 索引，并考虑去重只在最近 / 同会话候选里查找。**发现 7 已部分解决**：v2.3 新增 BM25 + RRF，默认检索改为 rrf。

**仍开放**：发现 1（加密覆盖范围）、3（删除非级联）、4（跨存储部分成功）、5/6（索引 fingerprint、SQLite 向量全库扫描）、11/12/13（REST 认证收紧、请求体校验、密钥生命周期）、14（MCP / REST / CLI 能力入口不一致）、15 中的 CI 与 pytest 化、immutable 记忆无限增长需冷存储归档。

回归：test_core 257/257、test_blockchain 72/72、test_governance 58/58、test_recall_policy 19/19、test_repo_map 14/14。

### 实施状态更新（2026-11-21，v2.1）

参考 MemoraX Code 的三类治理机制，本轮落地了以下能力（均为可选/增量，不改变既有默认行为）：

- `vecrecall/core/redaction.py` + `VecRecall(redact_payloads=True)`：写入前脱敏，部分缓解发现 1（加密范围）与发现 12（API 输入）中的隐私面；**不改变加密覆盖范围**，也不做 schema 校验。
- `vecrecall/core/writeback.py` + `vr writeback`：幂等键、四维缓冲、重试与 `writeback_outbox` 表，为发现 4 的 outbox 提供基础；**尚无跨后端原子性、级联删除或 tombstone**。
- `vecrecall/core/recall_policy.py` + `mp_recall_policy` / `mp_accept_memory`：检索门槛与三项验收，为发现 7 的可解释性提供结构化字段；**尚未实现分项分数、BM25/实体召回或 reranker**。
- `vecrecall/repomap/` + `vr repo-map`：新增仓库知识映射、基线增量检测与产物校验；该产物不进向量索引。

回归：原有 158/158、72/72 保持通过；新增治理 34/34、决策 19/19、repo-map 14/14。发现 2（namespace 隔离）、5（索引版本契约）、6（全库扫描）、10（图谱弱一致）等仍未解决。

## 2. 产品现状与成熟度

| 能力 | 当前实现 | 评估 |
|---|---|---|
| 写入与原文保留 | SQLite `KnowledgeGraph` 持久化记忆；可选抽取结构 | 基本闭环，跨组件写入缺少事务/补偿机制 |
| 嵌入与相似检索 | Bag-of-Words、SentenceTransformer、Ollama、OpenAI 兼容 API；SQLite/Chroma/内存向量 | 后端灵活；索引版本、过滤和规模边界不足 |
| 上下文构造 | L0/L1/L2/L3，重要性排序、语义阈值、可选 hybrid | 初步可用；L1 全库共享、重复结果、预算控制不足 |
| 记忆管理 | update/delete/prune/reindex/dedup/distill/recall | 功能较全；图谱、链和向量副本清理不完整 |
| 认知抽取 | 启发式或 LLM 抽取 topic/摘要/实体/关系/状态 | 有降级路径；结构验证、来源可信度和纠错反馈不足 |
| 时序图谱 | SQLite 实体、关系、状态及实体到记忆映射 | 可做原型探索；缺少撤销/失效、实体消歧、可见范围 |
| 隐私与安全 | Fernet 加密 `knowledge.db` 指定字段；REST bearer key | 有安全意识，但安全覆盖与承诺不匹配 |
| 接入 | Python、CLI、MCP、REST | 接入面有优势；兼容性、认证和集成示例需加强 |
| 质量保障 | `tests/` 自定义测试脚本；小型检索 benchmark | 有起点；尚无可见 CI 配置、基准规模小、没有质量门禁 |

主要实现位置：核心引擎及 lifecycle 在 [`vecrecall/core/engine.py`](vecrecall/core/engine.py)，图谱在 [`vecrecall/core/temporal_graph.py`](vecrecall/core/temporal_graph.py)，加密在 [`vecrecall/core/crypto.py`](vecrecall/core/crypto.py)，REST 在 [`vecrecall/api/server.py`](vecrecall/api/server.py)，MCP 在 [`vecrecall/mcp/server.py`](vecrecall/mcp/server.py)，内置召回评测在 [`benchmarks/benchmark_recall.py`](benchmarks/benchmark_recall.py)。

## 3. 关键审核发现

### P0：先处理的数据安全与完整性问题

1. **加密承诺覆盖范围不足。** `Cipher` 在 `KnowledgeGraph` 读写路径加密内容、摘要和 metadata，但 `graph.db` 保存实体名、关系、属性和值；SQLite/Chroma 向量库保留可关联记忆的向量和 ID；启用区块链时 `chain.db` 保存原文。加密开启后这些副本仍可能暴露敏感信息。当前 README 将其描述为静态加密，需改为准确的覆盖说明，或扩展加密覆盖并增加泄漏测试。参考 `engine.py` 的初始化/写入路径、`temporal_graph.py` 表结构以及 `crypto.py` 的 `Cipher.resolve`。
2. **多 wing 是组织标签，不是访问控制。** `search()`、L1 和记忆图谱都可跨 wing 返回数据；这符合项目当前“纯向量、无结构过滤”的设计，但在共享数据库或 REST 服务里会造成跨用户/项目数据串流。`dedup` 也在全库找候选，可能将另一个 wing 的记忆作为合并目标。产品应区分“全局个人库”与“受隔离的 namespace/tenant”，让过滤在召回前生效，并且对写入、读取、导出、图谱扩展和去重统一执行。
3. **删除并非完整删除。** `VecRecall.delete()` 删除向量和主记忆，但实体图谱的 `entity_mentions`、关系和属性状态没有按 `memory_id` 清理，链式归档也没有删除/保留策略。UI 删除后，图谱仍可能返回关联信息；隐私删除与备份保留策略需要明确。建议增加可审计的级联删除、派生数据重建和 tombstone/保留期策略。
4. **跨存储写入可能产生部分成功。** 新记忆依次写向量、主 SQLite、图谱和可选链；中间任一环节异常都会留下孤儿向量或不完整状态。删除、更新也不是跨后端原子操作。建议将主库作为事实源，使用事务性 outbox/可重试任务同步派生索引，并实现一致性检查与修复命令。

### P1：检索质量、可扩展性和行为正确性

5. **嵌入索引没有模型/版本契约。** 当前向量库未记录模型名、模型版本、维度、预处理版本；安装 sentence-transformers 前后自动选择的默认 backend 可能不同。更换模型后新旧向量混查会导致语义空间不兼容；维度变化还可能令 Chroma 查询失败。应在库元数据记录 embedding fingerprint，启动时拒绝静默混用，提供可回滚的双索引迁移。
6. **SQLite 向量检索为全库扫描。** `SqliteVectorBackend.query()` 每次读出全部向量并在 Python 中算余弦，时间和内存随数据量线性增长。作为个人小库的零依赖后端合理，但必须设定和公开规模边界，并提供 ANN/服务端向量后端路径、批量嵌入与性能基准。
7. **“hybrid”排序只混合相似度、importance 和 recency。** 没有关键词/BM25、实体信号、跨编码器 reranker，也没有分项得分解释；此外候选先按向量取 top-N 再 rerank，低相似但高相关的候选永远进不了重排。新增可选 lexical/BM25、实体/时间过滤、可插拔 reranker，并返回 score breakdown。
8. **遗忘曲线没有影响默认搜索。** 当前 `ForgettingCurve` 用于强度查询、复习与按强度清理；默认 `search()`/L1 排序不使用强度，因此文档里“检索时用强度”与实际路径不一致。应选择其一：把强度加入可选检索策略并建立评测，或修正文档明确这是生命周期工具。
9. **蒸馏流程可能重复执行、来源/wing 不严谨。** `distill(dry_run=False)` 写完摘要才给来源标 `distilled_into`，但下一轮筛选只跳过 `distilled` 的条目；源条目仍参与聚类，重复蒸馏风险较高。`_write_distilled()` 使用当前实例 wing，而计划本身没有来源 wing。应持久化蒸馏任务状态、来源集合、版本与幂等键；确认后才标记源记忆，且支持回滚和人工审阅。
10. **图谱是弱一致的派生表。** 实体规范化仅小写折叠空格；实体消歧、关系置信度/来源、事实有效期、反向更正、撤销与删除传播均较弱。当前同名实体容易错并，属性“最后写入值”会覆盖当前快照，即使旧事实时间戳较新；`neighbors` 也在内存加载全量关系再 BFS。应将图谱定义成可重建投影，保存 provenance、valid-from/valid-to、confidence，并对规模加入索引和遍历限制。

### P1：API 和数据输入安全

11. **REST 的默认本地认证是合理的，但远程使用需要收紧。** 非本地 host 强制 key 是好默认；然而 key 也接受 URL `?key=`，容易进入代理/服务器日志和历史记录。建议只接受 Authorization bearer，拒绝 URL key，支持速率/请求体上限、认证失败限流和更明确的 HTTP 错误结构。由于使用标准库 `http.server`，应标注其适用场景，生产部署推荐反向代理或成熟 ASGI 服务。
12. **请求体和参数校验不完整。** `_read_json()` 没有大小上限，错误 JSON 静默当空对象，分页 limit、`n`、阈值、导入行数和字符串长度无统一约束；大请求可能耗尽内存或占用 SQLite 锁。增加 schema 校验、上限、明确错误码、超时和并发/负载测试。
13. **密钥与密文生命周期需说明。** 密码派生盐以 `salt.key` 写入但无显式权限/原子创建/备份引导；丢失盐或密钥的恢复路径没有产品化。Fernet 兼容明文旧记录有助迁移，但需要提供“检测明文残留、迁移验证、轮换密钥”的命令与报告。

### P2：开发者体验、兼容性和信任

14. **工具定义与能力入口可能不一致。** MCP 工具、REST 路由、CLI 命令均有大量能力，但它们并非完全同构；维护者需面对多个重复入口。建立 capability matrix 和自动一致性检查，MCP 对外只暴露有稳定 schema 的接口。
15. **质量体系不足以支持当前宣传强度。** 仓库没有发现 `.github/workflows` CI 配置；测试是自定义脚本，基准只有 21 个 query、15 个 distractor，且每个 query 只有一个正确文档。README 的 100% 是该小型集合上的 R@5，不代表通用语义检索，也不适合直接与竞争产品 benchmark 比较。增加独立数据集、领域切片、MRR/nDCG/Recall、置信区间、延迟和成本，并在 CI 中固定可复现的轻量质量门禁。
16. **公开文档的技术承诺需校准。** README 对原版 MemPalace 的召回损失结论、现版 100% 召回、遗忘曲线用于检索、静态加密覆盖范围都应逐条给出处、代码路径或实验方法；删除不可复现的比较或明确“内部小型样例结果”。英文/中文文档同步更新，并把安装示例和版本号纳入发布校验。

## 4. 同类产品对标（能力维度）

以下对标依据各项目公开文档描述其功能，不比较其商业服务 SLA、实际质量或性能；产品功能更新较快，实施前应复核链接。

| 产品/项目 | 公开能力特征 | VecRecall 可借鉴 | VecRecall 的机会点 |
|---|---|---|---|
| [Mem0 OSS / Platform](https://docs.mem0.ai/core-concepts/memory-operations/search) | 按 user/agent/run 等 ID 过滤，语义检索结合 metadata filters，可配置 reranker；Graph Memory 将向量与图关系并行提供 | 首先把 tenant/user namespace 作为一级对象；输出 score details；过滤/重排可配置 | 更清晰的本地数据主权、简洁自托管与零服务依赖；避免复制整套托管平台功能 |
| [Zep Graphiti](https://help.getzep.com/graphiti/getting-started/welcome) | 事件/episode 驱动的时序上下文图，关系随新事实演进；公开文档强调语义、全文和图遍历的混合检索及 MCP | 事实有效期、冲突解析、增量图索引、混合召回和关系 provenance | 小型本地工作负载更易部署；把 SQLite 图谱保留为可选轻量方案，清楚说明和图数据库的规模差异 |
| [Letta](https://docs.letta.com/) | 面向 stateful agents；持久 memory blocks 常驻上下文，archival memory 可搜索/分页，blocks 可共享或附着到不同 agent | 区分常驻核心记忆与按需归档；显式的记忆容量预算、agent scope 和可控共享 | VecRecall 可作为不绑模型/agent runtime 的独立 memory backend，通过 MCP/API 接入多种 agent harness |
| [LangMem](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/) | 将 semantic、episodic、procedural 记忆分开；manager 能插入、更新、删除已有记忆，支持后台/热路径和自定义 schema；namespace 支持组织、用户、应用隔离 | 由写入流程管理“更新/删除/合并”，不只做追加；明确记忆类型及其读写策略；支持异步抽取 | VecRecall 的通用存储和 CLI 可独立于 LangGraph；可提供轻量 adapter，而不将框架设为必需依赖 |

### 竞争定位建议

- **不要把“纯向量无结构过滤”定义为不可变的唯一优点。** 全局回忆时这种策略减少漏召回，但多用户、多项目、安全边界和精准路由需要预过滤。产品应提供两种明确模式：`global` 全库探索和 `scoped` 隔离召回；默认安全模式应是 scoped。
- **把优势表述为可验证的工程属性：** 本地优先、数据格式可导出、可选云模型、主流程无需托管服务、可通过 MCP/CLI 接入。避免“比 X 召回率高”的断言，除非双方在同一公开基准、同一候选库和同一 token/延迟条件下比较。
- **专注易自托管的小型部署体验。** Mem0、Zep 和 Letta 已在持续拓展平台、图谱或 agent runtime 能力；VecRecall 的差异化更可能来自低依赖、自包含、透明可迁移和易审计，而非功能清单更长。

## 5. 详细改进路线

### 阶段 0：承诺校准与风险封口（1–2 周）

**目标：** 使文档准确反映现状，避免用户把原型功能误当成生产安全保障。

- 编写数据流和落盘清单：列出 `knowledge.db`、`vectors.db`/Chroma、`graph.db`、`chain.db`、配置、salt、导出文件各自存放什么。
- 修订 README 中 at-rest 加密范围、benchmark、forgetting curve 和竞争比较的表述。
- 为 `global`/`scoped` 两种检索语义写 RFC；禁止把 `wing` 继续暗示成隔离边界。
- 定义数据删除政策：主记录、向量、图谱、链式审计、导出和备份的行为分别是什么。
- 为 SQLite 向量 backend 和内置 benchmark 标明规模/方法限制。

**验收：** 数据流图与文件清单完整；README 的每项关键数字能链接至脚本、测试或公开实验；所有安全声明均明确覆盖对象和不覆盖对象。

### 阶段 1：数据正确性与隐私边界（2–4 周）

**目标：** 让写、更新、删除、导入和恢复在异常情况下可预测。

- **Namespace 模型：** 引入 `namespace_id`（可映射 user/agent/project/run）；所有检索、上下文、图谱扩展、列表、导入导出、去重和 L1 聚合接受 namespace scope。默认客户端实例绑定单一 namespace；跨 namespace 搜索需显式授权。
- **隔离测试：** 覆盖相同文本跨 namespace、dedup、batch、diary、graph search、MCP、REST 的泄漏情形；通过负向测试证明没有越界结果。
- **主库与投影：** 将主记忆库设为唯一事实源；通过事务性 outbox 或提交日志异步更新向量/图谱/哈希索引。提供 `vr doctor` 检出孤儿、缺失向量和索引模型不一致，提供可重试修复。
- **级联删除：** 图谱为每个实体、边、状态保留来源 `memory_id`；删除记忆时清理/重建其派生数据。链式审计采用清晰策略（保留哈希证明但移除内容，或显式不可擦除档案并告知用户）。
- **加密：** 给主记录和图谱/向量/链路定义分级保护方案。可先实现 SQLite page-level 加密或敏感 payload 加密；向量本身也可能泄露语义，应让用户选择不建图/不存向量。加入密钥轮换、明文残留扫描和备份恢复指南。
- **失败安全：** 注入故障验证每一步失败后的状态与重试幂等；批量操作支持事务或返回逐条成功/失败结果。

**验收：** 所有 namespace 隔离用例通过；`doctor` 能检测并修复人工制造的孤儿记录；删除后图谱/API 无法再返回被删记忆内容；加密开启时每个存储文件的敏感字段均有明确处理结果。

### 阶段 2：检索与记忆质量（3–6 周）

**目标：** 从“能搜索”进化为可解释、可对比、可调优的召回系统。

- **索引契约：** 持久化 embedder provider、模型名/版本、维度、归一化方式、文本处理版本、创建时间。启动发现不兼容时显式报错；`reindex --new-index` 构建新索引、验收后原子切换并允许回退。
- **召回管线：** 拆成候选召回（dense、BM25/SQLite FTS、metadata/时间、实体）→融合→可选 reranker→去重/多样性→预算截断。组件可关闭，默认轻量模式仍可不依赖外部服务。
- **可解释性：** 返回 dense/lexical/entity/recency/importance 分项分数、触发的过滤条件和源记忆 ID；支持 `explain=True`。
- **时间与事实：** 存储 event time 与 ingestion time，支持有效期/被纠正关系；查询“当时发生什么”和“现在是什么”时分开处理。属性冲突不可简单依赖最后写入顺序。
- **记忆类型：** semantic fact、episodic event、procedural preference/rule、常驻 profile 分别建 schema 或类型标签；允许基于类型配置留存、敏感级别和召回通道。
- **蒸馏治理：** 只对同一 namespace/主题/时间段的候选聚类；dry-run 展示来源和压缩损失，执行前可审批；成功后源条目状态持久化，任务幂等，允许撤销/重算。
- **评测集：** 建立不与开发 prompt 重叠的公开或合成评测集，加入多条正确证据、时序/纠错、同名实体、长尾实体和跨语言场景；分用户/项目/个人偏好等切片。报告 Recall@1/5/10、MRR、nDCG、错误跨域率、P50/P95 延迟、索引大小和 embedding 成本。

**建议门槛：** 每个发布版本在固定测试集上 Recall@5 不低于上一版 1 个百分点以上的回退；scoped 越界返回为 0；索引 1 万条记忆的本地 P95 查询时间形成公开基线。具体 SLA 需先在目标硬件测量后制定，不能用未经验证的主机数字承诺。

### 阶段 3：可靠性与开发者体验（4–8 周）

**目标：** 降低集成和升级成本，让用户能安全长期运行。

- 增加 GitHub Actions：支持 Python 3.10–当前支持版本矩阵、可选依赖组合、Windows/Linux、静态检查、打包与安装 smoke test；轻量 benchmark 防止质量倒退。
- 将自定义测试迁移/封装为标准 pytest；单测、后端契约、API/MCP 集成、属性测试与故障注入分层。加密和隔离必须进入 CI。
- API 输入统一 schema 校验：最大 body、最大 batch、字符数、limit/n 上限、阈值范围、结构深度；JSON 错误返回 400，不默认为空对象。认证 token 仅走 header；对外监听增加速率限制说明。
- 做数据库 schema migrations、版本兼容检查、自动备份/恢复演练；文档包括升级、重建索引、密钥丢失、数据导出与恢复。
- 统一 CLI/API/MCP 行为和错误结构；维护可生成的能力对照表和 MCP 工具版本策略。
- 建立可观测性：写入成功/降级率、embedding 错误、索引漂移、检索延迟、图谱规模、删除待清理量；默认不记录原文，隐私字段可关。
- 提供 Python SDK 使用示例和最小 Agent adapters（MCP、LangGraph/LangMem、OpenAI-compatible 工具定义）；每个集成都说明版本和权限边界。

**验收：** CI 全绿并可从新环境安装；备份恢复演练通过；API/MCP 能力矩阵自动检查；所有写入降级和索引修复有机器可读统计但默认不输出原始记忆内容。

### 阶段 4：可选规模化与生态（后续按用户需求）

- 在真实用户规模与吞吐要求出现后，再提供 PostgreSQL + pgvector 或其他 ANN backend；保持 SQLite 是单机默认选择。
- 多用户 REST 服务需有真正的 tenant authz、密钥管理、审计、配额、备份恢复、删除工作流和威胁建模；在此之前不要把单进程 REST 直接当成多租户服务。
- 增加 webhook/异步抽取队列、用户纠错与记忆来源查看、人工审核待处理队列。
- 评估文件系统记忆、profile/block 常驻记忆、Graphiti adapter 等互通方式；保持数据导出格式稳定，避免将用户锁定在某种 agent runtime。

## 6. 建议优先级与依赖关系

| 优先级 | 工作项 | 原因 | 依赖 |
|---|---|---|---|
| P0 | namespace 隔离与 scoped 检索 | 避免跨项目/用户错误召回 | API schema 变更、向后兼容策略 |
| P0 | 加密覆盖清单、删除语义和派生数据清理 | 隐私承诺可信，删除结果一致 | 主库/投影治理 |
| P0 | 索引 fingerprint 与安全重建 | 防止换模型后静默错搜/崩溃 | 存储 schema migration |
| P1 | outbox、doctor、幂等修复 | 解决多后端部分提交 | 主库确立为事实源 |
| P1 | 可解释 hybrid retrieval + 评测体系 | 用证据调优，而不是堆功能 | namespace、benchmark corpus |
| P1 | API 参数限制、header-only auth | 保护本地服务边界 | REST schema 校验 |
| P2 | 时序事实模型、图谱冲突/撤销 | 让“temporal graph”真实可用 | provenance 和评测用例 |
| P2 | distillation 状态机与审批 | 降低信息误压缩/重复压缩 | 生命周期事件模型 |
| P2 | CI、迁移、恢复与集成适配 | 支持可持续发布和开发者采用 | 前述行为契约稳定 |

## 7. 发布前必须回答的问题

1. `wing` 是浏览标签、隔离边界还是两者兼有？建议明确拆成 `namespace` 与 `project/wing` 标签。
2. VecRecall 的目标用户是单人本地库，还是团队/服务端多租户？建议先完成单用户多 namespace，再讨论服务端多租户。
3. 哈希链的不可变性与用户删除权如何取舍？加密删除销毁密钥是否算删除？
4. 默认启用抽取与去重会不会把用户未授权内容发送到远程 API？远程模型必须显式选择，并让数据流在初始化界面/文档可见。
5. 原文保存、摘要、图谱、向量分别有什么保留周期？是否提供敏感内容排除规则？

## 8. 结论

VecRecall 的强项是轻量、可自托管、多入口和愿意把记忆形成/整理/检索放在同一工具箱中；它已经超过单纯向量 demo，但还未达到可以把“安全、隔离、时序一致、生产可扩展”作为完成态宣传的程度。建议接下来一个版本将主题聚焦为**可靠的本地记忆底座**：先做 namespace、删除/加密边界、索引版本和一致性修复，再用公开评测证明召回质量，之后再拓展服务端规模与图谱高级能力。

## 参考资料

- [Mem0：Search Memory 与 namespace/filter/reranker 说明](https://docs.mem0.ai/core-concepts/memory-operations/search)
- [Mem0：Graph Memory](https://docs.mem0.ai/open-source/features/graph-memory)
- [Zep Graphiti：官方文档入口与时序图谱/混合检索概述](https://help.getzep.com/graphiti/getting-started/welcome)
- [Letta：官方文档与 stateful agent 定位](https://docs.letta.com/)
- [Letta：Archival Memory 列表/搜索 API](https://docs.letta.com/api/typescript/resources/agents/subresources/passages/methods/list)
- [LangMem：概念指南、记忆类型、namespace 与管理模式](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)
- [VecRecall 内置召回评测](benchmarks/benchmark_recall.py)
