**English** | [中文](#中文)

# VecRecall 区块链上下文存档

VecRecall 的区块链上下文存档模块。

将 AI 模型的上下文窗口按区块存档，突破单次上下文限制，让 AI 在进入下一个上下文窗口时仍能访问历史记忆。

---

## 核心思路

```
上下文窗口 1 ─→ 存档为区块 #0
上下文窗口 2 ─→ 存档为区块 #1，同时注入区块 #0 的关键片段
上下文窗口 3 ─→ 存档为区块 #2，同时注入区块 #0、#1 的关键片段
...
理论上无限叠加，AI 始终知道所有历史上下文
```

**触发时机：**
- **自动触发**：上下文接近使用上限时自动存档，为后续对话预留空间（阈值可配置）
- **手动触发**：用户主动存档重要对话
- **压缩前触发**：上下文压缩前强制存档，压缩不等于遗忘

**存储结构：**
- 小区块：一个上下文窗口的完整记录；相邻小区块会自动逐级合并为大区块，便于长期浏览
- 检索：按日期与关键词索引历史区块

**不可篡改性：**
- 每个区块与前一个区块构成哈希链，任何篡改都会导致链条断裂
- 可随时校验任意链的完整性

**按主题分链：**
- 链的链接与验证按 `(wing, topic_key)` 独立进行；写入记忆时可指定 `topic_key`
- 默认链向后兼容，老数据库打开时自动升级
- 适合把需要单独留证的记忆按主题分链（如 `topic_key="release:v2.2"`），一个主题的证据可以独立验证和导出，不受其他主题写入影响

---

## 安装

已作为 VecRecall 子模块包含，无需单独安装：

```bash
pip install vecrecall
```

---

## 对接平台

### OpenClaw

```json
{
  "plugins": {
    "vecclaw_blockchain": {
      "kind": "memory",
      "path": "~/.openclaw/extensions/vecclaw_blockchain",
      "config": {
        "db_path": "~/.vr/blockchain/openclaw.db",
        "wing": "openclaw-agent",
        "context_window_size": 2000000,
        "auto_trigger_threshold": 0.75
      }
    }
  }
}
```

触发点：
- `on_before_compaction`：压缩前自动存档
- `on_session_end`：会话结束存档
- `check_auto_trigger`：在 prompt 构建前检测自动触发条件

### Hermes Agent

按平台插件方式接入，在配置中指定存档数据库位置与 wing，即可获得：
- 上下文压缩前自动存档，并返回历史摘要注入新窗口
- 会话结束时存档

### Claude Code（MCP 工具）

在 `vr-mcp` 的基础上新增 5 个区块链工具：

| 工具 | 说明 |
|------|------|
| `bc_archive` | 手动存档当前上下文 |
| `bc_context` | 获取历史区块注入新窗口 |
| `bc_search` | 按日期与关键词检索区块 |
| `bc_stats` | 查看区块链状态 |
| `bc_verify` | 验证哈希链完整性 |

---

## Python API

```python
from vecrecall.blockchain import BlockChain

chain = BlockChain(db_path="~/.vr/blockchain/chain.db")

# 存入新区块
block = chain.new_block(
    content="完整对话内容...",
    wing="my-project",
    keywords=["数据库", "架构", "postgresql"],
)

# 按关键词与日期检索
results = chain.search_by_keywords(
    ["数据库", "架构"],
    date_start="2026-04-01",
    date_end="2026-04-30",
)

# 写入指定主题的独立子链
block = chain.new_block(
    content="发布决策：v2.2 冻结检索接口",
    wing="my-project",
    topic_key="release:v2.2",   # 与默认链互相独立
)

# 验证链完整性（默认链与指定主题链）
ok, msg = chain.verify_chain("my-project")
ok, msg = chain.verify_chain("my-project", topic_key="release:v2.2")
blocks = chain.get_blocks_by_topic("my-project", "release:v2.2")

# 统计
stats = chain.stats("my-project")
print(f"区块数: {stats['total_blocks']}")
print(f"总 token: {stats['total_tokens']:,}")
```

---

English | [Back to top](#vecrecall-区块链上下文存档)

# VecRecall Blockchain Archive

The blockchain context-archive module of VecRecall.

It archives AI context windows as blocks, breaking the single-context limit so that the AI can still access its history after moving to the next context window.

## How it works

- Context windows are archived as chained blocks; earlier blocks' key excerpts are injected into new windows, so history can stack indefinitely.
- Archiving triggers automatically when the context nears its limit (configurable threshold), manually, or right before context compaction — compaction does not mean forgetting.
- Small blocks are merged into larger ones automatically for long-term browsing; blocks are indexed by date and keywords.
- Each block links to the previous one; any tampering breaks the chain, and any chain can be verified at any time.
- Chains are split and verified per `(wing, topic_key)`; memories can carry a `topic_key` so a topic's evidence can be verified and exported independently (default chain stays backward compatible, old databases upgrade automatically).

## Platforms

- **OpenClaw / Hermes Agent**: plugin-based integration; configure the archive database path and wing to get pre-compaction archiving, session-end archiving, and history summary injection.
- **Claude Code (MCP)**: five extra tools on top of `vr-mcp` — `bc_archive`, `bc_context`, `bc_search`, `bc_stats`, `bc_verify`.

## Python API

```python
from vecrecall.blockchain import BlockChain

chain = BlockChain(db_path="~/.vr/blockchain/chain.db")
chain.new_block(content="...", wing="my-project", keywords=["postgres"])
chain.search_by_keywords(["postgres"], date_start="2026-04-01")
chain.verify_chain("my-project", topic_key="release:v2.2")
```
