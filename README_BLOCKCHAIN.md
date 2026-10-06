**English** | [中文](#中文)

# VecRecall Blockchain

VecRecall 的区块链上下文存档模块。

将 AI 模型的上下文窗口按区块存档，突破单次上下文限制，让 AI 在进入下一个上下文窗口时仍能访问历史记忆。

---

## 核心思路

```
上下文窗口 1（200万 token）─→ 存档为区块 #0
上下文窗口 2（200万 token）─→ 存档为区块 #1，同时注入区块 #0 的关键片段
上下文窗口 3（200万 token）─→ 存档为区块 #2，同时注入区块 #0、#1 的关键片段
...
理论上无限叠加，AI 始终知道所有历史上下文
```

**触发时机：**
- **自动触发（75%）**：上下文使用量达到 75% 时预触发存档，留 25% 空间
- **手动触发**：用户主动存档重要对话
- **压缩前触发**：上下文压缩前强制存档，压缩不等于遗忘

**存储结构：**
- 小区块：一个上下文窗口的完整记录
- L1 大区块：4 个小区块自动合并
- L2 超大区块：4 个 L1 大区块自动合并
- 检索凭证：日期 + 关键词索引

**不可篡改性：**
- 每个区块包含前一个区块的 SHA-256 哈希
- 任何篡改都会导致哈希链断裂
- `verify_chain()` 可随时验证完整性

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
- `check_auto_trigger`：在 `before_prompt_build` 中检测 75% 阈值

### Hermes Agent

```yaml
hooks:
  gateway:
    - type: plugin
      path: /path/to/vecrecall/blockchain/hooks.py
      class: HermesBlockchainHook
      config:
        db_path: ~/.vr/blockchain/hermes.db
        wing: hermes-agent
```

触发点：
- `on_precompact`：压缩前存档并返回历史摘要注入
- `summarize_session`：会话结束存档

### Claude Code（MCP 工具）

在 `vr-mcp` 的基础上新增 5 个区块链工具：

| 工具 | 说明 |
|------|------|
| `bc_archive` | 手动存档当前上下文 |
| `bc_context` | 获取历史区块注入新窗口 |
| `bc_search` | 按日期+关键词检索区块 |
| `bc_stats` | 查看区块链状态 |
| `bc_verify` | 验证哈希链完整性 |

---

## Python API

```python
from vecrecall.blockchain import BlockChain, create_hook

# 直接使用区块链
chain = BlockChain(db_path="~/.vr/blockchain/chain.db")

# 存入新区块
block = chain.new_block(
    content="完整对话内容...",
    wing="my-project",
    trigger="auto_75",
    keywords=["数据库", "架构", "postgresql"],
)

# 按关键词检索
results = chain.search_by_keywords(
    ["数据库", "架构"],
    date_start="2026-04-01",
    date_end="2026-04-30",
)

# 验证链完整性
ok, msg = chain.verify_chain("my-project")

# 统计
stats = chain.stats("my-project")
print(f"区块数: {stats['total_blocks']}")
print(f"总 token: {stats['total_tokens']:,}")

# 使用平台 Hook
hook = create_hook("openclaw", {
    "db_path": "~/.vr/blockchain/chain.db",
    "wing": "openclaw-agent",
    "context_window_size": 2_000_000,
})
```

---

## 文件结构

```
vecrecall/blockchain/
  __init__.py    模块入口
  block.py       Block / BlockGroup 数据结构
  chain.py       BlockChain 哈希链管理
  indexer.py     关键词提取（中英文）
  hooks.py       三平台触发器
```

---

## 测试

```bash
python tests/test_blockchain.py
# 结果: 72/72 通过
```
