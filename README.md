# VecRecall v2.2

[![版本](https://img.shields.io/badge/version-2.2.0-blue)](https://github.com/snowfoxHQ/vecRecall)
[![许可证](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-brightgreen)](https://python.org)

> 完整版本记录见 [CHANGELOG.md](CHANGELOG.md)。

认知记忆系统，也可作为多 Agent 的记忆运行时。基于对原版 MemPalace 的设计分析重新构建，v2.0 新增 **LLM 认知层**（写入时自动抽取实体/关系/摘要/话题 + 语义去重合并）、**时序知识图谱**、**遗忘曲线与主动蒸馏**、**多跳向量+图谱混合检索**、**静态加密（at-rest）**、**语义嵌入升级**（subword 增强词法 + 多语言语义模型）。

**v2.2 新增运行时原语**（全部可选、有默认值，单 Agent 用法不变）：

- **类型化记忆**：`memory_type` = episodic / semantic / procedural。semantic 必进时序图谱；procedural（规则、阈值、策略）按「场景标签 + 版本」精确匹配，不走向量模糊召回。
- **隔离**：`wing` × `visibility`（private/shared）× `owner`。private 仅写入者可见，shared 同 wing 可见，跨 wing 关联只对 shared 开放。
- **不可变记忆与分链**：`retention_policy="immutable"` 的记忆不去重、不遗忘、不被 prune、不参与蒸馏，始终进入 L1；哈希链按 `topic_key` 分链，单个主题的证据可独立验证。

## 与原版的核心区别

| | 原版 MemPalace | VecRecall |
|--|--|--|
| 检索路径 | 向量 + Room 元数据过滤 | **纯向量，无结构过滤** |
| L2 触发 | Room 名称匹配 | **语义相似度阈值（默认 0.55）** |
| AAAK 摘要 | 参与检索索引 | **只写 UI 层，不进向量库** |
| Wing/Topic | 同时影响检索和展示 | **只影响 UI 组织和浏览** |
| 召回率（R@5） | ~84%（启用全部特性时） | **语义嵌入 100% / 零依赖词法 76.2%（内置评测集）** |

原版最大的问题：**信息组织层和检索路径耦合在一起**。Room 过滤让检索召回率从 96.6% 降到 89.4%，AAAK 参与检索后进一步降到 84.2%。VecRecall 把两件事彻底分开：检索走向量，组织走 SQLite UI 层。

---

## 安装

```bash
# 基础版（零依赖，词法语义嵌入 + SQLite 持久化向量）
pip install -e .

# 生产版（真实语义嵌入 + ChromaDB 持久化）
pip install -e ".[full]"

# 静态加密（记忆内容磁盘加密，需要 cryptography）
pip install -e ".[crypto]"
```

## 快速开始

### Python API

```python
from vecrecall import VecRecall

with VecRecall(base_dir="~/.vr", wing="my-project") as palace:
    # 写入记忆（逐字存储原文）
    palace.add(
        content="决定采用 PostgreSQL 替代 MySQL，原因是 JSON 支持更好",
        topic="database",
        importance=0.9,
        ui_summary="DB迁移→PG",   # AAAK 摘要，只供 UI 展示，不参与检索
    )

    # 语义搜索（纯向量，无结构过滤）
    results = palace.search("数据库选型", n=5)

    # 构建四层上下文，直接注入 AI prompt
    ctx = palace.build_context(current_query="今天要继续讨论数据库问题")
    print(ctx.l0_identity)        # L0: ~50 tokens
    print(len(ctx.l1_key_moments))  # L1: top-15 关键时刻
    print(len(ctx.l2_topic_context))  # L2: 语义触发的相关上下文

    # 类型化写入（v2.2）
    palace.add("供应商 Acme 进入合格名录", memory_type="semantic")
    palace.add("付款超 5 万需双人复核", memory_type="procedural",
               scenario_tags=["payment", "gatekeeping"], policy_version="v1")
    palace.query_procedural(scenario_tags=["gatekeeping"], version="v1")

    # 不可变记忆：不去重、不遗忘、不被 prune，始终可召回；按 topic_key 独立成链
    palace.add("发布决策：v2.2 冻结检索接口", retention_policy="immutable",
               topic_key="release:v2.2")
```

### 多 Agent 示例（v2.2）

```python
# 两个 Agent 各自持有实例，共享同一个数据目录
agent_a = VecRecall(base_dir="~/.vr", wing="team", owner="agent-a")
agent_b = VecRecall(base_dir="~/.vr", wing="team", owner="agent-b")

agent_a.add("A 的私有笔记", owner="agent-a")                # private：仅 A 可见
mem = agent_a.add("团队共识：代码评审需两人通过", visibility="shared",
                  memory_type="procedural")                 # shared：同 wing 可见

agent_b.search("私有笔记")    # → 看不到 A 的 private 记忆

# 原子变更（供外部编排调用）
agent_a.update_visibility(mem.id, "shared")
agent_a.update_retention_policy(mem.id, "immutable")
```

> 授权（谁能写 shared）由调用方或接口层决定，核心只负责按 owner / visibility 过滤。owner 为空的老数据视为公开；实例未配置 owner 时不过滤，单机行为与 v2.1 一致。

### CLI

```bash
# 初始化（--chain 启用哈希链防篡改，--embedder 选择嵌入后端）
vr init --dir ~/.vr --wing my-project --chain
vr init --embedder ollama --embed-model nomic-embed-text   # 本地 Ollama（零 Key）
vr init --embedder api --embed-model text-embedding-3-small # 远程 API（读 OPENAI_API_KEY）

# 添加记忆
vr add "修复了 auth 模块的 JWT 过期 bug" --topic auth --importance 0.85
echo "今天的会议记录..." | vr add - --topic meeting

# 语义搜索（--hybrid 融合重要性+时间新鲜度重排）
vr search "认证相关问题" --layer l3 --hybrid

# 构建四层上下文
vr context "我们之前讨论过的认证方案" --l3

# 查看系统状态
vr stats
vr wings
vr topics --wing my-project

# Agent 日记（不同 Agent 隔离）
vr diary write reviewer "发现 SQL 注入漏洞 #bug-456"
vr diary write architect "决定采用 CQRS 模式"
vr diary read reviewer "安全漏洞"

# 记忆生命周期管理
vr delete <记忆ID>                 # 删除（支持短 ID 前 8 位）
vr prune --min-importance 0.3 --older-than 90  # 清理低价值记忆（预览，加 --yes 确认）
vr reindex                          # 从原文重建向量索引

# 回吐记忆列表（结构化清单，供浏览/挑选/引用）
vr list --wing my-project --limit 20
vr list --topic auth --full         # 含原文

# 防篡改验证（需 --chain 启用）
vr verify

# 存档整段会话
vr archive session.json

# 导出 / 导入
vr export my-project --out backup.json
vr import backup.json
```

## 认知层（LLM 抽取 + 语义去重）

v2.0 新增：写入记忆时，用一个可插拔的 `MemoryExtractor` 自动抽取结构化信息，并对高度相似的内容做去重合并，避免记忆库膨胀。

### 抽取器（MemoryExtractor）

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

### 语义去重合并

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

### 时序知识图谱

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

### 遗忘曲线 & 主动蒸馏

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

### 多跳向量+图谱混合检索

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

### 语义嵌入升级

v2.0 的嵌入分两层，召回率（R@5）见 `benchmarks/benchmark_recall.py`（内置 21 组中英混合 query + 15 干扰文档，可复现）：

| 嵌入后端 | R@5 | 依赖 |
|--|--|--|
| 词法（`bow`，默认零依赖） | 76.2% | 无 |
| 语义（`sentence`，多语言 MiniLM） | 100% | `pip install -e ".[sentence]"` |

- **零依赖词法增强**：`BagOfWordsEmbeddingBackend` 从「整词 + 中文二元组」升级为「整词 + 英文字符 3/4-gram（subword）+ 中文三元组」，可捕获词形变化（running/run）与拼写变体（database/databases），无需任何外部依赖。
- **语义模型多语言化**：`sentence` / `auto` 默认模型从英文 `all-MiniLM-L6-v2` 升级为多语言 `paraphrase-multilingual-MiniLM-L12-v2`（中文友好），可用 `--embed-model` 覆盖。
- 评测集刻意包含「词面不重叠、语义等价」的近义改写（如「程序崩溃了怎么办」→「应用 crash 时的排查步骤」），因此词法 76.2% 是无语义模型的裸下限，接上语义模型即达 100%。第一版宣称的 96.6% 是「原版纯向量上限」的复现（旧评测集，无脚本）；v2 起改为可复现 benchmark 重新量化。

```bash
python benchmarks/benchmark_recall.py          # 词法
python benchmarks/benchmark_recall.py --all    # 词法 + 语义
```

---

### 静态加密（at-rest encryption）

记忆内容在磁盘上加密存储，防止数据库文件被直接读取时泄露原文。加密范围：`knowledge.db` 的记忆原文、AAAK 摘要、元数据（向量索引是嵌入非明文，图谱是派生结构）。

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
```

---

### 写入治理、检索决策与仓库知识映射

v2.1 增加四组可独立启用的治理机制，默认不改变既有行为：

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

### MCP 服务器（Claude Code / Gemini CLI）

```bash
# 启动 MCP stdio 服务器（--embedder 选择 ollama / api）
vr mcp --dir ~/.vr --wing my-project --embedder ollama

# 或直接
vr-mcp --dir ~/.vr --wing my-project --embedder ollama --embed-model nomic-embed-text
```

在 Claude Code 的 `mcp_config.json` 中配置：

```json
{
  "mcpServers": {
    "vecrecall": {
      "command": "vr-mcp",
      "args": ["--dir", "~/.vr", "--wing", "my-project"]
    }
  }
}
```

### HTTP REST API 服务器（传统软件接入）

除 MCP 外，内置一个零依赖的 HTTP JSON 接口，任何语言（curl / requests / Java / C# / Node / Go ...）都能接入：

```bash
# 启动 REST API 服务器（默认 http://127.0.0.1:8791）
# 生产环境务必用 --api-key 启用认证（或环境变量 VR_API_KEY）
vr api --dir ~/.vr --wing my-project --embedder ollama --api-key my-secret-token

# 或直接
vr-api --dir ~/.vr --port 8791 --api-key my-secret-token
```

**认证**：配置了 `--api-key`（或 `VR_API_KEY`）后，除 `/health` 外的所有请求都必须带令牌——请求头 `Authorization: Bearer <token>` 或查询参数 `?key=<token>`；否则返回 401。监听非本机地址（如 `--host 0.0.0.0`）时**强制要求**提供 token。

常用端点：

```bash
TOKEN=my-secret-token
BASE=http://127.0.0.1:8791

curl $BASE/health                                    # 健康检查（免认证）
curl -H "Authorization: Bearer $TOKEN" $BASE/memories      # 回吐记忆列表
curl -H "Authorization: Bearer $TOKEN" "$BASE/memories?topic=auth&full=1"
curl -H "Authorization: Bearer $TOKEN" $BASE/memories/<id>  # 单条记忆

curl -X POST $BASE/memories \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content": "决定采用微服务架构", "topic": "arch", "importance": 0.9}'

curl -X POST $BASE/search \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "架构决策", "n": 5, "hybrid": true}'

curl -X POST $BASE/context \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"current_query": "继续讨论架构", "load_l2": true}'

curl -X DELETE -H "Authorization: Bearer $TOKEN" $BASE/memories/<id>  # 删除
```

完整端点：`GET /stats` `/wings` `/topics`，`POST /memories/batch` `/prune` `/reindex` `/verify`，`PUT /memories/{id}`。所有响应均为 JSON。

---

## 安全防护

VecRecall 对接口访问提供多层守护：

| 防护 | 机制 |
|---|---|
| **REST 认证** | `--api-key` / `VR_API_KEY` 启用 Bearer Token（或 `?key=`），否则 401；监听非本机地址强制要求 token |
| **跨域防护** | 默认不发送 CORS 头，浏览器网页无法跨域盗读；需 `--cors-origin` 显式放行指定 Origin |
| **文件路径隔离** | MCP 的 `mp_import_json` / `mp_export_wing` 将路径限制在数据目录内，防止任意文件读写（含 prompt 注入诱导） |
| **SQL 注入防护** | 全部查询参数化（`?` 占位符），不拼接用户输入 |
| **防篡改（完整性）** | `--chain` 哈希链：每次写入生成区块，按 `(wing, topic_key)` 分链，`mp_verify` / `vr verify` 检测原文篡改 |
| **密钥不落盘** | 远程嵌入 API Key 只从环境变量读取，不写入 config.json |

**注意**：默认不加密，记忆原文以明文写入 `knowledge.db`。存放敏感内容时请启用静态加密（`vr init --encrypt` 或 `encryption_key`，见上文），或开启写入前脱敏（`redact_payloads=True`）。可用 `python scripts/audit_secrets.py` 只读扫描已入库记忆中是否混入凭据。

---

## 四层记忆栈

每次 AI 唤醒只加载 600-900 token，而不是把全部历史塞进 prompt。

```
L0  身份层          ~50 tokens   每次必加载，固定
L1  关键时刻        ~600 tokens  按 importance 排序的 top-15，不按 wing 过滤；immutable 记忆始终纳入
L2  语义触发上下文  ~300 tokens  当前对话与历史记忆相似度 ≥ 0.55 时加载
L3  深度检索        按需触发     全量语义检索，直接命中向量库
```

L2 的改动是关键：原版用 Room 名称匹配触发，VecRecall 改为语义相似度阈值。
阈值可调：`vr-mcp` 工具 `mp_set_l2_threshold`，或代码 `palace.L2_TRIGGER_THRESHOLD = 0.6`。

---

## MCP 工具列表（共 34 个）

**检索决策**
- `mp_recall_policy` — 判断当前任务是否值得检索，并给出规范化查询意图
- `mp_accept_memory` — 三项验收（同边界 / 同行为 / 可执行）；`mp_search` 的 `acceptance_query` 只附加验收、不改排序

**写入**
- `mp_add` — 写入单条记忆
- `mp_add_batch` — 批量写入
- `mp_update` — 更新记忆内容/话题/重要性/摘要（内容变化自动重向量化）
- `mp_update_importance` — 更新重要性评分
- `mp_delete` — 删除记忆
- `mp_prune` — 清理低价值记忆
- `mp_reindex` — 从原文重建全部向量索引

**检索（全部走纯向量，无结构过滤）**
- `mp_search` — 语义搜索
- `mp_build_context` — 构建四层上下文 bundle
- `mp_l1_moments` — 获取 L1 关键时刻
- `mp_l2_context` — L2 语义触发上下文
- `mp_l3_deep` — L3 全量深度检索
- `mp_fuzzy_recall` — 模糊回忆（低阈值宽松匹配）

**组织层（只用于 UI 浏览，不影响检索）**
- `mp_list_wings` — 列出所有 wing
- `mp_list_topics` — 列出话题
- `mp_list_memories` — 回吐记忆列表（结构化清单，含 ID/摘要/重要性/预览，可选原文）
- `mp_browse_wing` — 浏览某个 wing
- `mp_browse_topic` — 浏览某个话题
- `mp_get_memory` — 按 ID 获取记忆

**知识图谱**
- `mp_link` — 建立跨 wing 关联（v2.2 起仅对双方均为 shared 的记忆开放）

**Agent 日记**
- `mp_diary_write` — 写入 Agent 日记
- `mp_diary_read` — 读取 Agent 日记

**会话存档**
- `mp_archive_session` — 存档完整对话

**管理**
- `mp_stats` — 系统统计
- `mp_health` — 健康检查
- `mp_verify` — 验证哈希链完整性（防篡改）
- `mp_export_wing` — 导出 wing 数据
- `mp_import_json` — 导入 JSON
- `mp_set_identity` — 更新 L0 身份层
- `mp_set_wing` — 切换默认 wing
- `mp_set_l2_threshold` — 调整 L2 阈值
- `mp_format_prompt` — 格式化为可注入 prompt

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

---

## 隐私

- 全部本地运行，数据不上传
- 核心功能无需任何 API Key
- SQLite 存元数据和原文，向量库存嵌入向量
- 数据目录默认 `~/.vecrecall`，可自定义

---

## 测试

```bash
python tests/test_core.py
python tests/test_blockchain.py
python tests/test_governance.py
python tests/test_recall_policy.py
python tests/test_repo_map.py
```

`test_core.py` 含核心边界守护：核心源码不得出现场景 schema（如 `contract_id`），也不得引用 `vecrecall.adapters`。

---

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
