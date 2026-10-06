
[English](README_EN.md) | **中文**

## 更新说明 v1.0.4

**更新日期：** 2026年4月26日

### 本次更新内容

**新增 `vr add-file` 命令（大文件存入）**

解决 Windows 命令行 8191 字符限制导致长内容无法存入的问题。支持任意大小的文件直接存入记忆库，已验证 75,328 字符完整存入。

- 自动识别文件编码（UTF-8 / GBK / GB2312 等）
- 存入时显示文件大小、字符数和 token 估算
- 用法：`vr add-file D:\文件.txt --topic 话题 --importance 0.9`

**新增 `vr get` 命令（查看完整内容）**

按 ID 查看记忆的完整原文，不截断。支持完整 ID 和前 8 位短 ID。

- 用法：`vr get ID前8位`
- 显示完整内容、时间、wing、topic、字符数等信息

**新增 `vr browse` 命令（记忆目录）**

按日期浏览记忆目录，一行两段格式，不占空间。

- 用法：`vr browse` / `vr browse --all` / `vr browse --wing 项目名`
- 格式：`2026-04-26  |  [devlog] VecRecall 开发日志  [test] 测试记录`

### 完整工作流

```powershell
# 存入大文件
vr add-file D:\笔记.txt --topic notes --importance 0.9

# 浏览记忆目录
vr browse

# 语义搜索
vr search "关键词"

# 查看完整内容
vr get ID前8位
```


## 更新说明 v1.0.3

**更新日期：** 2026年4月25日

### 本次更新内容

**新增区块链上下文存档子模块（vecrecall/blockchain/）**

新增四个核心文件：

- `block.py` — 区块数据结构，每个区块包含完整原文、时间戳、关键词、SHA-256 哈希链
- `chain.py` — 哈希链管理，SQLite 持久化，支持日期+关键词检索，4个小区块自动合并为L1大区块
- `indexer.py` — 中英文关键词自动提取，滑动窗口覆盖中文子词，高权重词优先
- `hooks.py` — 三平台触发器，支持 OpenClaw、Hermes Agent、Claude Code

核心功能：将 AI 模型的上下文窗口按区块存档，突破单次上下文限制。上下文使用量达到 75% 时自动触发存档，压缩前强制存档，AI 进入下一个上下文窗口时可注入历史关键片段。

**新增文件**
- `vecrecall/blockchain/__init__.py`
- `vecrecall/blockchain/block.py`
- `vecrecall/blockchain/chain.py`
- `vecrecall/blockchain/indexer.py`
- `vecrecall/blockchain/hooks.py`
- `tests/test_blockchain.py`（72/72 通过）
- `README_BLOCKCHAIN.md`
- `README_EN.md`（新增英文版）


# VecRecall

改进版 AI 长期记忆系统。基于对原版 MemPalace 的设计分析重新构建。

# VecRecall v2.0

认知记忆系统。基于对原版 MemPalace 的设计分析重新构建，v2.0 新增 **LLM 认知层**（写入时自动抽取实体/关系/摘要/话题 + 语义去重合并）、**时序知识图谱**、**遗忘曲线与主动蒸馏**、**多跳向量+图谱混合检索**、**静态加密（at-rest）**、**语义嵌入升级**（subword 增强词法 + 多语言语义模型）。

## 与原版的核心区别

| | 原版 MemPalace | VecRecall |
|--|--|--|
| 检索路径 | 向量 + Room 元数据过滤 | **纯向量，无结构过滤** |
| L2 触发 | Room 名称匹配 | **语义相似度阈值（默认 0.55）** |
| AAAK 摘要 | 参与检索索引 | **只写 UI 层，不进向量库** |
| Wing/Topic | 同时影响检索和展示 | **只影响 UI 组织和浏览** |
<<<<<<< HEAD
| 召回率（R@5） | ~84%（启用全部特性时） | **96.6%+（纯向量基线）** |
| 召回率（R@5） | ~84%（启用全部特性时） | **语义嵌入 100% / 零依赖词法 76.2%（内置评测集）** |

原版最大的问题：**信息组织层和检索路径耦合在一起**。Room 过滤让检索召回率从 96.6% 降到 89.4%，AAAK 参与检索后进一步降到 84.2%。VecRecall 把两件事彻底分开：检索走向量，组织走 SQLite UI 层。


## 安装

```bash
<<<<<<< HEAD
# 基础版（零依赖，哈希嵌入 + 内存向量）
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
```

### CLI

```bash
# 初始化
vr init --dir ~/.vr --wing my-project

# 初始化（--chain 启用哈希链防篡改，--embedder 选择嵌入后端）
vr init --dir ~/.vr --wing my-project --chain
vr init --embedder ollama --embed-model nomic-embed-text   # 本地 Ollama（零 Key）
vr init --embedder api --embed-model text-embedding-3-small # 远程 API（读 OPENAI_API_KEY）

# 添加记忆
vr add "修复了 auth 模块的 JWT 过期 bug" --topic auth --importance 0.85
echo "今天的会议记录..." | vr add - --topic meeting


# 语义搜索
vr search "认证相关问题" --layer l3

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

### MCP 服务器（Claude Code / Gemini CLI）

```bash
# 启动 MCP stdio 服务器
vr mcp --dir ~/.vr --wing my-project

# 或直接
vr-mcp --dir ~/.vr --wing my-project
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


### MCP 服务器（Claude Code / Gemini CLI）

```bash
# 启动 MCP stdio 服务器（--embedder 选择 ollama / api）
vr mcp --dir ~/.vr --wing my-project --embedder ollama

# 或直接
vr-mcp --dir ~/.vr --wing my-project --embedder ollama --embed-model nomic-embed-text
>>>>>>> d636dfed8d506e0976414d5cb440e6499a643c6f
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


## 安全防护

VecRecall 对接口访问提供多层守护：

| 防护 | 机制 |
|---|---|
| **REST 认证** | `--api-key` / `VR_API_KEY` 启用 Bearer Token（或 `?key=`），否则 401；监听非本机地址强制要求 token |
| **跨域防护** | 默认不发送 CORS 头，浏览器网页无法跨域盗读；需 `--cors-origin` 显式放行指定 Origin |
| **文件路径隔离** | MCP 的 `mp_import_json` / `mp_export_wing` 将路径限制在数据目录内，防止任意文件读写（含 prompt 注入诱导） |
| **SQL 注入防护** | 全部查询参数化（`?` 占位符），不拼接用户输入 |
| **防篡改（完整性）** | `--chain` 哈希链：每次写入生成区块，`mp_verify` / `vr verify` 检测原文篡改 |
| **密钥不落盘** | 远程嵌入 API Key 只从环境变量读取，不写入 config.json |

**注意**：记忆原文以明文存储（无 at-rest 加密），敏感数据请配合磁盘加密或自托管部署。


## 四层记忆栈

每次 AI 唤醒只加载 600-900 token，而不是把全部历史塞进 prompt。

```
L0  身份层          ~50 tokens   每次必加载，固定
L1  关键时刻        ~600 tokens  按 importance 排序的 top-15，不按 wing 过滤
L2  语义触发上下文  ~300 tokens  当前对话与历史记忆相似度 ≥ 0.55 时加载
L3  深度检索        按需触发     全量语义检索，直接命中向量库
```

L2 的改动是关键：原版用 Room 名称匹配触发，VecRecall 改为语义相似度阈值。
阈值可调：`vr-mcp` 工具 `mp_set_l2_threshold`，或代码 `palace.L2_TRIGGER_THRESHOLD = 0.6`。

---

## MCP 工具列表（共 26 个）
=======
## MCP 工具列表（共 32 个）

**写入**
- `mp_add` — 写入单条记忆
- `mp_add_batch` — 批量写入
<<<<<<< HEAD
- `mp_update_importance` — 更新重要性评分
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
- `mp_link` — 建立跨 wing 关联

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
    embedding_backend=SentenceTransformerBackend("all-MiniLM-L6-v2"),
)
```

默认后端（零依赖）：`NumpyVectorBackend` + `HashEmbeddingBackend`（哈希向量，仅供开发测试，无真实语义）。

生产环境推荐：`ChromaVectorBackend` + `SentenceTransformerBackend`。
    embedding_backend=SentenceTransformerBackend(),  # 默认多语言 MiniLM，可传自定义模型名
    use_blockchain=True,   # 可选：写入时生成哈希链区块，防篡改审计,
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


## 隐私

- 全部本地运行，数据不上传
- 核心功能无需任何 API Key
- SQLite 存元数据和原文，向量库存嵌入向量
- 数据目录默认 `~/.vecrecall`，可自定义

---

## 测试

```bash
python tests/test_core.py
# 结果: 46/46 通过
```


## Windows 安装验证记录

以下为在 Windows 11 环境下的实际安装测试记录（2026年4月23日）。

**测试环境**

- 系统：Windows 11
- Python：3.10+
- 安装路径：`I:\Github\VecRecall`
- 数据目录：`C:\Users\admin\.vecrecall`

**安装步骤**

```powershell
# 克隆或下载项目后进入目录
cd I:\Github\VecRecall

# 基础安装
pip install -e .

# 安装生产级语义嵌入后端
pip install sentence-transformers chromadb
```

**验证输出**

```
PS I:\Github\VecRecall> vr --help
usage: vr [-h] {init,add,search,context,stats,wings,topics,diary,archive,export,import,mcp} ...
VecRecall — 改进版 AI 长期记忆系统
```

```
PS I:\Github\VecRecall> vr init
✓ VecRecall 初始化完成
  数据目录: C:\Users\admin/.vecrecall
  默认 wing: default
  当前记忆数: 0
```

```
PS I:\Github\VecRecall> vr stats
📊 VecRecall 状态
  记忆总数:    1
  Wing 数:     1
  跨关联数:    0
  数据目录:    C:\Users\admin/.vecrecall
  默认 wing:   default
  向量后端: ChromaVectorBackend      ← 安装 chromadb 后自动启用
  嵌入后端: SentenceTransformerBackend  ← 安装 sentence-transformers 后自动启用
```

**注意事项**

- 基础安装后默认使用 `HashEmbeddingBackend`（哈希嵌入，无真实语义），搜索功能受限
- 安装 `sentence-transformers` 和 `chromadb` 后，引擎自动切换到语义向量后端，无需任何配置
- `sentence-transformers` 首次运行时会下载模型文件（约 90MB），需要等待
- pip 提示新版本可用属于正常通知，不影响功能
- Windows 路径中反斜杠需注意转义，CLI 命令中使用正斜杠即可


## 更新说明 v1.0.1

**更新日期：** 2026年4月23日

### 本次更新内容

**1. 后端自动检测（核心改动）**

修复了 `engine.py` 中后端初始化逻辑。之前安装了 `sentence-transformers` 和 `chromadb` 之后仍然使用哈希嵌入，导致语义搜索无法正常工作。现在启动时会自动检测已安装的后端并切换：

- 检测到 `sentence-transformers` → 自动使用 `SentenceTransformerBackend`（真实语义嵌入）
- 检测到 `chromadb` → 自动使用 `ChromaVectorBackend`（持久化向量存储）
- 两者都没有 → 回退到 `HashEmbeddingBackend` + `NumpyVectorBackend`（开发测试用）

无需任何配置，安装完依赖包后重新运行即自动生效。

**2. README 补充 Windows 安装验证记录**

新增「Windows 安装验证记录」章节，记录在 Windows 11 环境下的实际安装测试情况，包括完整安装步骤、命令输出和注意事项，方便 Windows 用户参考。

### 升级方式

已安装旧版本的用户，只需替换 `vecrecall/core/engine.py` 一个文件，然后重新执行：

```powershell
pip install -e .
pip install sentence-transformers chromadb
```

重新运行 `vr stats`，确认后端显示为 `SentenceTransformerBackend` 即升级成功。

---

## 更新说明 v1.0.2

**更新日期：** 2026年4月23日

### 本次更新内容

**中英文编码自动适配（cli/main.py）**

修复 Windows PowerShell 下中文参数乱码问题。之前在 Windows 环境下用 `vr add` 存入中文内容时，由于 PowerShell 默认使用 GBK 编码传递参数，导致存入的中文变成乱码。

本次修改在 CLI 入口处增加自动编码检测和修正逻辑：

- 启动时自动检测 stdin/stdout/stderr 编码，非 UTF-8 环境自动切换
- Windows 下自动修正 sys.argv 中的中文参数编码
- 中英文混合内容均可正确处理
- 非 Windows 环境不受影响

### 升级方式

替换 `vecrecall/cli/main.py` 文件后重新执行：

```powershell
pip install -e .
```

升级后直接用 `vr add` 命令存入中文内容即可，无需额外处理。


## 区块链上下文存档模块（v1.0.3）

VecRecall 内置区块链子模块，将 AI 模型的上下文窗口按区块存档，突破单次上下文限制。

### 核心思路

```
上下文窗口 1（200万 token）─→ 存档为区块 #0
上下文窗口 2（200万 token）─→ 存档为区块 #1，同时注入区块 #0 的关键片段
上下文窗口 3（200万 token）─→ 存档为区块 #2，同时注入历史关键片段
...
理论上无限叠加，AI 始终知道所有历史上下文
```

### 存储结构

| 层级 | 说明 |
|------|------|
| 小区块 | 一个上下文窗口的完整记录 |
| L1 大区块 | 4 个小区块自动合并 |
| L2 超大区块 | 4 个 L1 大区块自动合并 |
| 检索凭证 | 日期 + 关键词索引 |

### 触发时机

- **自动触发（75%）**：上下文使用量达到 75% 时预触发存档
- **压缩前触发**：上下文压缩前强制存档，压缩不等于遗忘
- **手动触发**：用户主动存档重要对话

### 不可篡改性

每个区块包含前一个区块的 SHA-256 哈希，任何篡改都会导致哈希链断裂，`verify_chain()` 可随时验证完整性。

### 对接平台

支持 OpenClaw、Hermes Agent、Claude Code 三个平台，按 wing 隔离，互不干扰。详见 `README_BLOCKCHAIN.md`。

### 文件结构

```
vecrecall/blockchain/
  __init__.py    模块入口
  block.py       Block / BlockGroup 数据结构
  chain.py       BlockChain 哈希链管理
  indexer.py     关键词提取（中英文）
  hooks.py       三平台触发器
```

### 快速使用

```python
from vecrecall.blockchain import BlockChain, create_hook

# 创建区块链
chain = BlockChain(db_path="~/.vr/blockchain/chain.db")

# 存入区块
block = chain.new_block(
    content="完整对话内容...",
    wing="my-project",
    trigger="auto_75",
    keywords=["数据库", "架构"],
)

# 按关键词检索
results = chain.search_by_keywords(["数据库"], date_start="2026-04-01")

# 验证链完整性
ok, msg = chain.verify_chain("my-project")

# 使用平台 Hook
hook = create_hook("openclaw", {
    "db_path": "~/.vr/blockchain/chain.db",
    "wing": "openclaw-agent",
    "context_window_size": 2_000_000,
})
```

### 测试

```bash
python tests/test_blockchain.py
# 结果: 72/72 通过
```
# 结果: 158/158 通过

python tests/test_blockchain.py
# 结果: 72/72 通过
```

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
