# 接口参考

- [MCP](#mcp)
- [MCP 工具列表](#mcp-工具列表共-34-个)
- [REST](#http-rest-api)
- [CLI](#cli)

## MCP

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

## MCP 工具列表（共 37 个）

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

**检索（不按 wing / topic 过滤；`mp_search` 默认 rrf）**
- `mp_search` — 检索，`mode` 可选 rrf（默认）/ vector / bm25
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
- `mp_provenance` — 查看记忆来源（入口、Agent、会话、轮次、重复写入、取代与蒸馏关系）

**知识图谱**
- `mp_link` — 建立跨 wing 关联（v2.2 起仅对双方均为 shared 的记忆开放）

**Agent 日记**
- `mp_diary_write` — 写入 Agent 日记
- `mp_diary_read` — 读取 Agent 日记

**会话存档**
- `mp_archive_session` — 存档对话：默认每轮一条记忆、共用 session_id；`granularity=session` 时整段一条

**矛盾检测**
- `mp_contradictions` — 列出被新记忆取代的旧记忆（检索时降权，不删除）
- `mp_supersede` — `action=mark` 手动标记取代；`action=dismiss` 撤销误判

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

## HTTP REST API

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
  -d '{"query": "架构决策", "n": 5, "mode": "rrf"}'

curl -X POST $BASE/context \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"current_query": "为什么选微服务架构", "load_l2": true}'

curl -X DELETE -H "Authorization: Bearer $TOKEN" $BASE/memories/<id>  # 删除
```

完整端点：`GET /stats` `/wings` `/topics`，`POST /memories/batch` `/prune` `/reindex` `/verify`，`PUT /memories/{id}`、`GET /memories/{id}/provenance`（来源，v2.5）。认证只接受请求头 `Authorization: Bearer <key>`（v2.6 起拒绝 URL 里的 `?key=`）；请求体上限 8 MB（环境变量 `VR_API_MAX_BODY`），超出返回 413，非法 JSON 返回 400。`POST /memories` 可带 `provenance` 字段。所有响应均为 JSON。

`POST /search` 支持 `mode`（`rrf` 默认 / `vector` / `bm25`）、`hybrid`、`min_score`。

## CLI

```bash
# 初始化（--chain 启用哈希链防篡改，--embedder 选择嵌入后端）
vr init --dir ~/.vr --wing my-project --chain
vr init --embedder ollama --embed-model nomic-embed-text   # 本地 Ollama（零 Key）
vr init --embedder api --embed-model text-embedding-3-small # 远程 API（读 OPENAI_API_KEY）

# 添加记忆
vr add "登录接口的 JWT 有效期从 1 小时改成 24 小时" --topic auth --importance 0.85
echo "周会决定：下周三发布 v2.3" | vr add - --topic meeting

# 检索（默认 rrf；--mode vector|bm25|rrf；--hybrid 再按重要性与时间重排）
vr search "JWT 有效期是多少" --layer l3 --mode rrf

# 构建四层上下文（L1 关键记忆 + 与问题相关的记忆）
vr context "JWT 有效期多长，要不要改" --l3

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
vr check [--repair]                 # 检查主库与向量 / 图谱索引是否一致，--repair 修复
vr contradictions                   # 列出被取代的旧记忆
vr supersede <旧ID> <新ID>          # 手动标记取代；vr supersede <旧ID> --dismiss 撤销误判
vr prune --min-importance 0.3 --older-than 90  # 清理低价值记忆（预览，加 --yes 确认）
vr reindex                          # 从原文重建向量索引

# 回吐记忆列表（结构化清单，供浏览/挑选/引用）
vr list --wing my-project --limit 20
vr list --topic auth --full         # 含原文

# 防篡改验证（需 --chain 启用）
vr verify

# 存档会话（默认每轮对话一条记忆；--granularity session 为整段一条）
vr archive chat.json             # chat.json：[{"role": "user", "content": "..."}, ...]

# 导出 / 导入
vr export my-project --out backup.json
vr import backup.json
```

### 全部子命令

| 命令 | 说明 |
|---|---|
| `vr init` | 初始化 |
| `vr add` | 添加记忆 |
| `vr add-file` | 从文件存入记忆（支持大文件，自动识别编码） |
| `vr search` | 语义搜索 |
| `vr context` | 构建四层上下文 |
| `vr stats` | 系统状态 |
| `vr wings` | 列出所有 wing |
| `vr topics` | 列出话题 |
| `vr diary` | Agent 日记 |
| `vr archive` | 存档会话 JSON（默认每轮对话一条记忆） |
| `vr export` | 导出 wing 数据 |
| `vr import` | 从 JSON 导入 |
| `vr get` | 按 ID 查看记忆完整内容（支持短 ID 前8位） |
| `vr browse` | 按日期浏览记忆目录 |
| `vr delete` | 删除记忆（支持短 ID 前 8 位） |
| `vr prune` | 清理低价值记忆 |
| `vr verify` | 验证哈希链完整性 |
| `vr reindex` | 重建全部向量索引 |
| `vr mcp` | 启动 MCP 服务器 |
| `vr api` | 启动 HTTP REST API 服务器 |
| `vr list` | 回吐记忆列表（结构化清单） |
| `vr graph` | 列出时序知识图谱实体 |
| `vr entity` | 查询实体画像与状态演化 |
| `vr decay` | 遗忘评估（按遗忘曲线列出低强度记忆） |
| `vr distill` | 主动蒸馏（碎片化记忆压缩为抽象长期记忆） |
| `vr graph-search` | 多跳向量+图谱混合检索 |
| `vr writeback` | 查看/恢复写回队列 |
| `vr recall-policy` | 判断当前任务是否需要检索历史记忆 |
| `vr repo-map` | 仓库知识映射（构建/增量检测/校验） |
