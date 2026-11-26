# VecRecall 使用指南

README 只列功能，这里是每项功能的用法。接口清单见 [interfaces.md](interfaces.md)。

## 目录

- [会话存档](#会话存档)
- [检索模式](#检索模式)
- [认知层](#认知层llm-抽取--语义去重)
- [语义去重合并](#语义去重合并)
- [时序知识图谱](#时序知识图谱)
- [遗忘曲线 & 主动蒸馏](#遗忘曲线--主动蒸馏)
- [多跳检索](#多跳向量图谱混合检索)
- [类型化记忆与多 Agent 隔离](#类型化记忆与多-agent-隔离)
- [静态加密](#静态加密at-rest-encryption)
- [写入治理](#写入治理检索决策与仓库知识映射)
- [推理链与反思](#推理链与反思)
- [扩展钩子](#扩展钩子)
- [插件](#插件)
- [嵌入后端](#嵌入后端)
- [后端可插拔](#后端可插拔)

---

## 会话存档

```python
mems = palace.archive_session(messages, session_id="chat-0525")      # 默认每轮一条
one  = palace.archive_session(messages, granularity="session")       # 整段一条（v2.2 行为）
palace.session_memories("chat-0525")                                 # 按顺序取回整段会话
```

- 每轮一条记忆，共用 `session_id`，metadata 记录 `turn_index` 和 `role`；空轮次跳过。
- 逐轮模式默认关闭语义去重，避免不同会话里的相同语句被合并。
- CLI：`vr archive session.json [--granularity turn|session] [--session-id ID]`；MCP：`mp_archive_session` 的 `granularity` 参数。

---

## 检索模式

`search()` / `build_context()` 的 `mode` 参数（CLI `--mode`、MCP / REST `mode` 字段）：

| mode | 做法 | `score` 的含义 |
|---|---|---|
| `rrf`（默认） | 向量与 BM25 各取 n×3 条候选，按名次融合（k=60） | 融合分数，约 0.01–0.04 |
| `vector` | 纯向量检索 | 余弦相似度 |
| `bm25` | 纯 BM25 词法检索 | BM25 分数 |

- 依赖分数阈值的调用方请用 `mode="vector"`；`min_score` 只作用于向量那一路。
- 全局切回旧行为：`palace.DEFAULT_SEARCH_MODE = "vector"`。
- BM25 索引在第一次词法检索时从 SQLite 原文构建，之后随写入增量维护；`reindex()` 后重建。多个进程同时写同一个数据目录时，其他进程新写入的记忆要等本进程重建索引后才搜得到。
- `hybrid=True` 在结果上再按「0.6 相似度 + 0.25 重要性 + 0.15 时间新鲜度」重排；`as_of` 指定计算新鲜度的参考时刻。LongMemEval 上 hybrid 没有带来提升，默认关闭。
- L2 语义触发（0.55）和写入去重（0.92）始终按余弦相似度判断，不受 `mode` 影响。

---

## 认知层（LLM 抽取 + 语义去重）

写入记忆时，用一个可插拔的 `MemoryExtractor` 自动抽取结构化信息，并对高度相似的内容做去重合并，避免记忆库膨胀。

## 抽取器（MemoryExtractor）

| 抽取器 | 依赖 | 说明 |
|---|---|---|
| `heuristic` | 零依赖 | 关键词话题 + 启发式重要性 + 英文专有名词实体 |
| `ollama` | 本地 Ollama | 调用 `/api/chat` 让 LLM 抽取实体/关系/摘要/话题/重要性 |
| `api` | 远程 API | 调用 OpenAI 兼容 `/chat/completions` |

```bash
# 启用 LLM 认知层（本地 Ollama，零 API Key）
vr init --extractor ollama --llm-model llama3.2

# 或远程 OpenAI 兼容 API
vr init --extractor api --llm-model gpt-4o-mini --llm-api-key $LLM_KEY

# 之后所有 vr add 都会自动抽取 topic/重要性/摘要/实体/关系
vr add "Alice 加入了微服务团队，负责 Kubernetes 部署"

# 也可以临时覆盖（不写 config）
vr add "..." --extractor ollama --llm-model llama3.2
```

抽取结果自动填充记忆的 `topic`、`importance`、`ui_summary`，实体与关系存入 `metadata.entities` / `metadata.relations`（为后续时序知识图谱预留）。LLM 调用失败时自动降级启发式，写入永不阻塞。

## 来源追踪

v2.5 起，每条新记忆在 `metadata["provenance"]` 里记下它从哪里来：

| 字段 | 含义 |
|---|---|
| `via` | 写入入口：`python`（默认）/ `mcp` / `rest` / `cli` / `distill` / `reasoning`，可自定义 |
| `agent` | 写入者：`add(owner=...)`，没传时用实例的 `owner` |
| `session_id` / `turn_index` / `role` | 会话与轮次（`archive_session()` 自动填写） |
| `created_at` | 写入时间，由引擎维护，调用方不能覆盖 |
| 其他 | 调用方补充的任意字段，如 `source_ref`（PR 号、文档路径、消息 ID） |

```python
m = palace.add("评审意见：users 表要加索引", owner="reviewer",
               provenance={"source_ref": "PR#42", "channel": "github"})

palace.provenance(m.id)
# {"id": ..., "origin": {"via": "python", "agent": "reviewer", "source_ref": "PR#42", ...},
#  "occurrences": 1, "merged_from": [], "superseded_by": None, "supersedes": []}
```

- **重复写入**：去重合并时，首次来源保留在 `origin`，之后每次重复写入的来源记在 `merged_from`（最多 `PROVENANCE_MAX_MERGES` = 20 条，超出部分计入 `merged_dropped`）。
- **取代与蒸馏**：`provenance()` 同时返回 `superseded_by` / `supersedes`；蒸馏出的记忆 `via=distill`，`derived_from` 列出源记忆，源记忆有 `distilled_into`。
- **旧数据**：v2.4 及更早写入的记忆没有 provenance，`provenance()` 会从 `session_id`、`owner`、写入时间推断，并标记 `legacy: True`。
- **可见性**：看不到的 private 记忆，`provenance()` 同样报「不存在」。

MCP：`mp_add` 支持 `provenance` 参数，`mp_provenance` 查看来源；REST：`POST /memories` 支持 `provenance` 字段，`GET /memories/<id>/provenance`；CLI：`vr get <ID>` 显示来源。

## 矛盾检测

v2.4 起默认开启。新记忆推翻某条旧记忆时，旧记忆在 metadata 里记下 `superseded_by`（以及原因、置信度、时间），新记忆记下 `supersedes`，检索时旧记忆的分数乘以 `SUPERSEDED_WEIGHT`（默认 0.5）再重排。**不删除任何记忆。**

```python
old = palace.add("我们决定用 PostgreSQL 做主库")
new = palace.add("主库从 PostgreSQL 换成 MySQL")
palace.get(old.id).metadata["superseded_by"] == new.id   # True

palace.search("主库用什么")                          # 新记忆排在前面
palace.search("主库用什么", include_superseded=True)  # 不降权，原样排序
palace.contradictions()                              # 全部取代记录
palace.unsupersede(old.id)                           # 误判时撤销
palace.supersede(old_id, new_id, reason="人工确认")   # 规则没识别时手动标记
```

**规则判断**（默认，零依赖）要同时满足三个条件，原则是宁可漏判、不误判：

1. 新记忆带变更信号：「改用 / 换成 / 改到 / 不再 / switched / changed to / no longer」等；「现在 / now」这类弱信号要求更高的主题重合度。没有信号的写入直接跳过，不做额外检索。
2. 两条记忆说的是同一件事：去掉变更词后的内容词重合度足够高。
3. 旧值被换掉了：只是在旧记忆上补充信息（「PostgreSQL 主库的连接池设为 20」）不算。

候选是向量相似度在 `CONTRADICTION_MIN_SIM`（0.2）到去重阈值（0.92）之间的前 5 条，限同 wing、对写入者可见、未被取代的记忆。immutable 记忆不参与（既不会被取代，也不会去取代别的记忆）；metadata 带 `_no_contradiction: True` 的单条写入跳过检测。

**局限**：没有共同词的变更识别不了，例如「我住在西雅图」→「我上个月搬去了波士顿」。这类情况可以接 LLM（v2.6 起内置）：

```python
from vecrecall import VecRecall, LLMExtractor

# 复用认知层的 LLM：规则先判断，判不了再问 LLM
palace = VecRecall(base_dir="~/.vr",
                   extractor=LLMExtractor(provider="ollama", model="qwen2.5:7b"),
                   contradiction_judge="llm")
```

CLI：`vr init --extractor ollama --llm-model qwen2.5:7b --contradiction-judge llm`。

LLM 只判断进入候选范围的记忆（向量相似度 ≥ 0.2）。默认的词法嵌入下，没有共同词的两条记忆相似度很低（「I live in Seattle」与「I moved to Boston last month」只有 0.12），进不了候选，LLM 也看不到。要覆盖这类情况，请同时使用语义嵌入（默认 MiniLM 或 e5-small）。

也可以单独配置，或传自己的判断函数：

```python
from vecrecall.core.contradiction import LLMJudge, Verdict

judge = LLMJudge.from_config(provider="api", model="gpt-4o-mini", api_key="...",
                             min_confidence=0.7)
palace = VecRecall(base_dir="~/.vr", contradiction_judge=judge)

def my_judge(old_text: str, new_text: str):
    ...   # 返回 Verdict(...) 或 None
palace = VecRecall(base_dir="~/.vr", contradiction_judge=my_judge)
```

开销控制：接了 LLM 后每次写入都会找候选，但只对向量相似度 ≥ `judge_min_similarity`（0.3）的候选调用 LLM，每次写入最多 `max_judge_calls`（2）次；规则已经判定的不再调用。LLM 置信度低于 `min_confidence`（0.7）不算推翻；网络失败、超时、输出不是 JSON 时按「不矛盾」处理，写入不受影响。关闭检测：`VecRecall(..., detect_contradictions=False)`。

MCP：`mp_contradictions`、`mp_supersede`；CLI：`vr contradictions`、`vr supersede`。

## 语义去重合并

默认开启：写入前先做向量检索，若与既有记忆相似度 ≥ `dedup_threshold`（默认 0.92），则合并（`occurrences + 1`、重要性取较大值），而不是新增一条。

```python
m1 = palace.add(content="决定采用微服务架构")
m2 = palace.add(content="决定采用微服务架构")   # → 合并，返回 m1
assert m1.id == m2.id
assert m2.metadata["occurrences"] == 2

palace.add(content="决定采用微服务架构", dedup=False)  # 显式关闭去重
```

Python 侧也可直接构造：

```python
from vecrecall import VecRecall, LLMExtractor

with VecRecall(base_dir="~/.vr", wing="my-project",
               extractor=LLMExtractor(provider="ollama", model="llama3.2"),
               dedup_threshold=0.92) as palace:
    palace.add("Alice 加入了微服务团队")
```

## 时序知识图谱

认知层抽取出的实体、关系、状态自动汇入一张**带时间戳的知识图谱**（`graph.db`），记录实体的出现、实体间的关联，以及实体属性随时间的演化。

```python
palace.add("Alice 加入了微服务团队")   # 状态：Alice.team = 微服务团队
palace.add("Alice 转岗到平台团队")     # 状态：Alice.team = 平台团队（时间线新增一条）

alice = palace.entity("Alice")
print(alice["attributes"])      # {'team': '平台团队'}  ← 当前快照
for s in alice["timeline"]:     # 状态演化，按时间升序
    print(s["attribute"], s["value"], s["timestamp"])
for r in alice["relations"]:    # 直接相连的关系
    print(r["source"], r["type"], r["target"])

palace.list_entities()          # 按提及次数降序
palace.graph_stats()            # {'entities': N, 'entity_states': M, 'relations': K}
```

命令行查询：

```bash
vr graph              # 列出实体（按提及次数）
vr entity Alice       # 查看 Alice 的画像 + 状态演化 + 关联关系
```

> 零依赖下（`HeuristicExtractor`）图谱会自动登记英文专有名词实体及同段共现关系；启用 LLM 抽取器后（`--extractor ollama/api`）还能获得中文实体、语义关系与实体属性状态，实现真正的「谁在什么时候是什么状态」。

## 遗忘曲线 & 主动蒸馏

记忆强度按**艾宾浩斯遗忘曲线**指数衰减：重要性越高衰减越慢、复习过的记忆衰减更慢。强度跌破阈值的记忆进入「已遗忘」状态，可被清理或压缩。

```python
palace.memory_strength(mem.id)   # 当前强度 0-1（新记忆≈1）
palace.forgotten(0.2)            # 已遗忘记忆（强度 < 0.2）
palace.recall(mem.id)            # 复习一次：间隔效应增强记忆

# 主动蒸馏：把同一话题下碎片化的低价值记忆压缩成一条抽象长期记忆
plans = palace.distill(topic="diary", dry_run=True)    # 先预览计划
palace.distill(topic="diary", dry_run=False)           # 真正执行
```

- 蒸馏记忆携带 `metadata.distilled=True` + `sources=[...]`，源记忆标记 `distilled_into`。
- 无 LLM 时用启发式拼接摘要；启用 `LLMExtractor` 后自动调用 LLM 生成凝练摘要。
- `prune(use_forgetting=True)` 按遗忘曲线清理（重要记忆衰减慢，天然难被误删）。

命令行：

```bash
vr decay                       # 遗忘评估
vr distill --topic diary       # 主动蒸馏（dry-run）
vr distill --topic diary --yes # 真正执行
```

## 多跳向量+图谱混合检索

在向量语义检索之外，叠加知识图谱的**多跳关系扩展**：向量召回种子 → 沿实体关系图向外扩展关联实体 → 汇总可解释的证据路径。

```python
res = palace.graph_search("Alice", hops=2)
res["direct"]     # 向量直接命中
res["graph"]      # 图谱扩展发现的关联记忆（含 entities / paths）
# paths 示例: Alice -co-occurs-> Google -co-occurs-> DeepMind
```

```bash
vr graph-search "Alice" --hops 2
```

---

## 类型化记忆与多 Agent 隔离

- **类型化记忆**：`memory_type` = episodic / semantic / procedural。semantic 必进时序图谱；procedural（规则、阈值、策略）用 `query_procedural(scenario_tags, version)` 按「场景标签 + 版本」精确匹配，不走向量模糊召回。`build_context(memory_types=[...])` 一处过滤 L1/L2/L3。
- **隔离**：`wing` × `visibility`（private / shared）× `owner`。private 只有写入者能看到，shared 同 wing 可见，跨 wing 关联只对双方都是 shared 的记忆开放。`add()` 没传 `owner` 时用实例的 `owner`（v2.5 起；之前会留空）。owner 为空的记忆视为公开；实例没配 owner 时不过滤。
- **不可变记忆与分链**：`retention_policy="immutable"` 的记忆不去重、不遗忘、不被 prune、不参与蒸馏，始终进入 L1；哈希链按 `(wing, topic_key)` 分链，单个主题的记录可以单独验证（`verify_integrity(topic_key=...)`、`query_by_topic_key(prefix)`）。

```python
from vecrecall import VecRecall

with VecRecall(base_dir="~/.vr", wing="my-project") as palace:
    # 类型化写入（v2.2）
    palace.add("供应商 Acme 进入合格名录", memory_type="semantic")
    palace.add("付款超 5 万需双人复核", memory_type="procedural",
               scenario_tags=["payment", "gatekeeping"], policy_version="v1")
    palace.query_procedural(scenario_tags=["gatekeeping"], version="v1")

    # 不可变记忆：不去重、不遗忘、不被 prune，始终可召回；按 topic_key 独立成链
    palace.add("发布决策：v2.2 冻结检索接口", retention_policy="immutable",
               topic_key="release:v2.2")
```

### 多 Agent 隔离

```python
# 两个 Agent 各自持有实例，共享同一个数据目录
agent_a = VecRecall(base_dir="~/.vr", wing="team", owner="agent-a")
agent_b = VecRecall(base_dir="~/.vr", wing="team", owner="agent-b")

agent_a.add("A 的私有笔记")                # private：仅 A 可见（v2.5 起默认 owner=agent-a）
mem = agent_a.add("团队共识：代码评审需两人通过", visibility="shared",
                  memory_type="procedural")                 # shared：同 wing 可见

agent_b.search("私有笔记")    # → 看不到 A 的 private 记忆

# 原子变更（供外部编排调用）
agent_a.update_visibility(mem.id, "shared")
agent_a.update_retention_policy(mem.id, "immutable")
```

> 授权（谁能写 shared）由调用方或接口层决定，核心只负责按 owner / visibility 过滤。owner 为空的老数据视为公开；实例未配置 owner 时不过滤，单机行为与 v2.1 一致。

---

## 静态加密（at-rest encryption）

记忆内容在磁盘上加密存储，防止数据库文件被直接读取时泄露原文。加密范围只有 `knowledge.db` 里记忆的原文、AAAK 摘要、元数据。

```python
from vecrecall import VecRecall, Cipher

key = Cipher.generate_key()          # 生成随机密钥（务必妥善保存）
palace = VecRecall(base_dir="~/.vr", encryption_key=key)
palace.add("数据库密码是 hunter2")    # 落盘时 content/摘要/元数据已加密
```

- 密钥来源：直接给 Fernet 密钥（44 字符 urlsafe base64），或给密码（PBKDF2 派生，盐持久化在 `salt.key`）。
- 错误密钥读取会抛 `InvalidToken`（绝不静默返回密文）；已存在的明文旧数据自动兼容。
- 依赖 `cryptography`（`pip install vecrecall[crypto]`），未安装时启用加密会给出明确提示，绝不静默降级为明文。

命令行：

```bash
vr init --encrypt                    # 生成密钥并写入配置
vr init --encrypt-key "你的密码"      # 或指定密钥/密码
VR_ENCRYPTION_KEY="..." vr add "..."  # 环境变量运行时覆盖（对所有命令生效）
vr encryption status                 # 覆盖范围、明文残留、不加密的文件
vr encryption rotate --new-key "..."  # 轮换密钥（同时补加密明文旧数据），并写入配置
```

**不加密的文件**（`vr encryption status` 会列出实际存在的）：

| 文件 | 内容 | 说明 |
|---|---|---|
| `graph.db` | 实体名、关系、属性值 | 开了认知层或写入 semantic 记忆时才有内容，可能直接暴露人名、项目名 |
| `vectors.db` / `chroma/` | 记忆向量 | 不是明文，但有研究表明可以部分反推原文 |
| `chain.db` | 哈希链区块，含原文 | 只在开启哈希链时存在；敏感数据请不要同时开链 |
| `reasoning/`、`traces/` | 推理链、Trace 事件 | 只在启用对应插件时存在；Trace 默认只记元数据 |

`lexical.db`（BM25 词频，约等于原文）在加密库里不会生成。对这些文件有要求时，请把整个数据目录放在加密卷上（BitLocker、FileVault、LUKS）。

**密钥轮换**：`palace.rotate_key(new_secret)` 用新密钥重写全部记忆，单个事务完成，失败整库回滚；加密前写入的明文旧记录会一起补加密。之后打开这个库必须用新密钥。密码模式下 `salt.key` 原子创建、权限 0600、已存在时不覆盖；**盐丢失等于密钥丢失**，请和密码一起备份。

---

## 写入治理、检索决策与仓库知识映射

四组可独立启用的治理机制，默认不改变既有行为：

- **写入前脱敏**：`redact_memory_payload()` 用带优先级的规则表识别私钥、令牌、Cookie、API Key、邮箱等 8 类敏感信息，白名单避免误伤 `${VAR}` / `example`。`VecRecall(redact_payloads=True)` 在落库前脱敏，并把统计写入 `metadata.redaction_counts`。脱敏不可逆，默认关闭；与静态加密互补（加密不阻止原文写入）。
- **写回治理**：`WritebackGovernor` 提供幂等键、按 turn / 字符 / 消息数 / idle 四维触发的缓冲和失败重试；`SqliteOutbox` 把待写回任务持久化到 `knowledge.db` 的 `writeback_outbox` 表，可用 `vr writeback stats|recover` 查看与恢复。
- **检索决策**：`decide_recall(query, context)` 判断当前任务是否值得检索，`build_query_intent()` 规范化查询，`evaluate_memory()` 做三项验收（同边界 / 同行为 / 可执行）。MCP 暴露 `mp_recall_policy`、`mp_accept_memory`；`mp_search` 的 `acceptance_query` 会附加验收结果但**不改变召回和排序**。
- **仓库知识映射**：`vr repo-map build|update|validate` 把仓库结构、入口、模块职责与 git 证据写入仓库内 `.vecrecall/repo/`，按 baseline SHA 增量更新并校验产物。该产物**不进入向量索引**，只作为显式上下文，保持"检索与组织解耦"。

```python
from vecrecall import (
    VecRecall, redact_memory_payload, decide_recall,
    evaluate_memory, build_repo_map, validate_repo_map,
)

print(decide_recall("之前那个 API 迁移为什么失败了？").should_recall)  # True
palace = VecRecall("~/.vr", redact_payloads=True)
palace.add("token = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789'")

report = build_repo_map(".")
print(report["git_status"], validate_repo_map(".")["ok"])
```

```bash
vr recall-policy "之前那个 API 迁移为什么失败了？"
vr writeback stats
vr repo-map build --repo .
vr repo-map update --repo .
vr repo-map validate --repo .
```

---

## 推理链与反思

`vecrecall.reasoning`（可选模块，移植自 HMR ThoughtChain）记录「为什么这样决定」：一次推理任务是一条链，链上依次挂观察、假设、决策、行动；事后用实际结果反思，标出哪些决策对、哪些错，并把有学习价值的结论写进主库。

```python
from vecrecall.reasoning import ReasoningEngine, ThoughtType

eng = ReasoningEngine(palace)
c = eng.start("排查 IPC 队列超时")
eng.think(c.chain_id, "队列深度持续超过 800", ThoughtType.OBSERVATION)
eng.think(c.chain_id, "消费者线程不足", ThoughtType.HYPOTHESIS)
eng.think(c.chain_id, "扩容消费者线程 x3", ThoughtType.DECISION)
eng.reflect(c.chain_id, "延迟从 800ms 降到 50ms", rating=0.9)

eng.best_decision_for("IPC 队列积压超时")
# {'decision': '扩容消费者线程 x3', 'accuracy': 1.0, ...}
```

- 链保存在独立的推理库 `<base_dir>/reasoning/`，推理过程不会出现在主库的检索结果里。
- 评分 ≥ 0.7 时决策判为正确；≤ 0.3 时决策和假设判为错误。评分 > 0.85 或 < 0.25 时，结论作为 semantic 记忆写进主库（`topic="reasoning_insight"`）；写入失败时留在链上，`retry_pending_insights()` 可以补写。
- `find_similar()` 用 rrf 检索取候选，再要求目标的词重叠 ≥ 0.3；`best_decision_for()` 只采信准确率 ≥ 0.7 的链。
- 遵守 owner / visibility 隔离；主库加密时推理库也加密。

---

## 扩展钩子

`vecrecall.core.hooks`，外部扩展包可以在不修改核心代码的前提下挂载行为。默认不注册任何钩子，行为不变。

| 调用点 | 触发时机 | 签名 | 典型用途 |
|---|---|---|---|
| `before_write` | 写入前 | `fn(fields) -> fields \| None`，抛异常即拒绝写入 | 写入授权、打租户标记、字段改写 |
| `after_write` | 写入后（含去重合并） | `fn(memory, merged)` | 审计记录、同步到其他系统 |
| `recall_filter` | 检索结果返回前 | `fn(memories, viewer) -> memories`，只能删减 | 读取授权、租户隔离 |
| `on_delete` | 删除后 | `fn(memory_id)` | 审计、级联清理 |

`recall_filter` 作用于 `search`、`build_context`（L1/L2/L3）、`query_procedural`、`query_by_topic_key` 与 `graph_search`。

```python
from vecrecall import VecRecall

palace = VecRecall(base_dir="~/.vr", wing="team", owner="agent-a")

# 1. 直接注册回调
def deny_secrets(fields):
    if "内部机密" in fields["content"]:
        raise PermissionError("不允许写入")
palace.hooks.register("before_write", deny_secrets)
palace.hooks.register("after_write", lambda mem, merged: print("写入", mem.id, merged))

# 2. 插件对象：实现 register(registry)
class TenantPlugin:
    def register(self, registry):
        registry.register("recall_filter",
                          lambda mems, viewer: [m for m in mems
                                                if m.metadata.get("tenant") == "t1"])
palace.hooks.install(TenantPlugin())
```

也可以通过 entry point 自动发现插件（组名 `vecrecall.plugins`），需显式开启 `VecRecall(load_plugins=True)`：

```toml
# 扩展包的 pyproject.toml
[project.entry-points."vecrecall.plugins"]
my_plugin = "my_package.plugin:TenantPlugin"
```

核心只定义调用点，不包含任何授权、合规或业务逻辑。

---

## 插件

插件都通过[扩展钩子](#扩展钩子)挂载，不改核心。

### Trace：本地事件日志

```python
from vecrecall.plugins import enable_trace
trace = enable_trace(palace)            # 写到 <base_dir>/traces/events-YYYY-MM-DD.jsonl
trace.store.read_events(limit=20)
trace.store.stats()
```

- 记录写入、去重合并、删除、召回四类事件；默认只记元数据，`capture_content=True` 时才记原文，先脱敏再截断。
- 按天分文件，默认保留 7 天；单文件超过上限后拆分。
- Trace 写入失败只计数，不影响记忆的写入和检索。
- 已知限制：零结果的检索不留记录；召回事件不含查询原文。

### 记忆卡片门槛

编码 Agent 可以把经验写成以 `CODE_AGENT_MEMORY` 开头的结构化卡片：

```text
CODE_AGENT_MEMORY
repo: snowfoxHQ/vecRecall
scope: vecrecall/core/engine.py::search
status: verified
signal: test_core 257/257 通过
problem: search 默认 min_score=0 丢弃负分结果
failed_shape: 只改了 _deep_search，漏了 search 的默认值
validation: python tests/test_core.py
principle: 检索默认返回 top-n，阈值由调用方显式给出
anchors: tests/test_core.py::test_search_returns_topn_with_negative_scores
```

```python
from vecrecall.plugins import enable_memory_cards, list_cards
enable_memory_cards(palace)                # 严格：不合格直接拒绝
enable_memory_cards(palace, strict=False)  # 宽松：降级为 candidate 再写入
list_cards(palace, status="verified", repo="snowfoxHQ/vecRecall")
```

- `status: verified` 必须写 `validation`，且 `signal` 不能为空或 unknown；`failed_attempt` 必须写 `failed_shape`；`problem` 不能为空。
- 对所有写入入口生效（Python、MCP、REST、CLI）；卡片的状态以卡片内容为准，调用方不能通过 metadata 覆盖；卡片不参与语义去重。
- 不是卡片的普通内容照常写入。

---

## 嵌入后端

- **零依赖词法增强**：`BagOfWordsEmbeddingBackend` 从「整词 + 中文二元组」升级为「整词 + 英文字符 3/4-gram（subword）+ 中文三元组」，可捕获词形变化（running/run）与拼写变体（database/databases），无需任何外部依赖。
- **语义模型多语言化**：`sentence` / `auto` 默认模型从英文 `all-MiniLM-L6-v2` 升级为多语言 `paraphrase-multilingual-MiniLM-L12-v2`（中文友好），可用 `--embed-model` 覆盖。

召回率数字以 README 的「召回率」一节和 [benchmarks/README.md](../benchmarks/README.md) 为准。

---

## 后端可插拔

```python
from vecrecall.core.engine import (
    VecRecall,
    ChromaVectorBackend,      # 需要 pip install chromadb
    SentenceTransformerBackend,  # 需要 pip install sentence-transformers
)

palace = VecRecall(
    base_dir="~/.vr",
    wing="prod",
    vector_backend=ChromaVectorBackend("~/.vr/chroma"),
    embedding_backend=SentenceTransformerBackend(),  # 默认多语言 MiniLM，可传自定义模型名
    use_blockchain=True,   # 可选：写入时生成哈希链区块，防篡改审计
)
```

**嵌入后端双重选择（本地 Ollama / 远程 API）**，用 `build_embedding_backend` 工厂统一构造：

```python
from vecrecall import VecRecall, build_embedding_backend

# 本地 Ollama（零 API Key，数据不出本机；推荐 nomic-embed-text / bge-m3）
emb = build_embedding_backend("ollama", model="nomic-embed-text")

# 远程 API（OpenAI 兼容；api_key 缺省读环境变量 OPENAI_API_KEY）
emb = build_embedding_backend(
    "api", model="text-embedding-3-small",
    base_url="https://api.openai.com/v1",
)

palace = VecRecall(base_dir="~/.vr", embedding_backend=emb)
```

工厂取值：`auto`（默认，有 sentence-transformers 则用，否则词法）/ `bow`（零依赖词法）/ `sentence`（本地模型）/ `ollama`（本地 Ollama）/ `api`（OpenAI 兼容远程）。CLI 与 MCP / REST 服务器均可通过 `--embedder` 选择。

默认后端（零依赖）：`SqliteVectorBackend`（SQLite 持久化向量，重启不丢索引）+ `BagOfWordsEmbeddingBackend`（特征哈希语义嵌入，中文单字+二元组+三元组、英文字符 3/4-gram subword，可捕获词形/拼写变体）。默认安装即可获得跨会话持久化和零依赖的语义召回，无需任何外部依赖。

生产环境推荐：`ChromaVectorBackend` + `SentenceTransformerBackend`（更深层语义）。

更换嵌入后端后，运行一次 `vr reindex`（或 `mp_reindex`）从原文重建全部向量索引即可迁移旧数据。
