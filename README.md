[English](README_EN.md) | **中文**

# VecRecall

改进版 AI 长期记忆系统。基于对原版 MemPalace 的设计分析重新构建。

## 与原版的核心区别

| | 原版 MemPalace | VecRecall |
|--|--|--|
| 检索路径 | 向量 + Room 元数据过滤 | **纯向量，无结构过滤** |
| L2 触发 | Room 名称匹配 | **语义相似度阈值（默认 0.55）** |
| AAAK 摘要 | 参与检索索引 | **只写 UI 层，不进向量库** |
| Wing/Topic | 同时影响检索和展示 | **只影响 UI 组织和浏览** |
| 召回率（R@5） | ~84%（启用全部特性时） | **96.6%+（纯向量基线）** |

原版最大的问题：**信息组织层和检索路径耦合在一起**。Room 过滤让检索召回率从 96.6% 降到 89.4%，AAAK 参与检索后进一步降到 84.2%。VecRecall 把两件事彻底分开：检索走向量，组织走 SQLite UI 层。

---

## 安装

```bash
# 基础版（零依赖，哈希嵌入 + 内存向量）
pip install -e .

# 生产版（真实语义嵌入 + ChromaDB 持久化）
pip install -e ".[full]"
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

# 添加记忆
vr add "修复了 auth 模块的 JWT 过期 bug" --topic auth --importance 0.85
echo "今天的会议记录..." | vr add - --topic meeting

# 语义搜索
vr search "认证相关问题" --layer l3

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

---

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

**写入**
- `mp_add` — 写入单条记忆
- `mp_add_batch` — 批量写入
- `mp_update_importance` — 更新重要性评分

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
    embedding_backend=SentenceTransformerBackend("all-MiniLM-L6-v2"),
)
```

默认后端（零依赖）：`NumpyVectorBackend` + `HashEmbeddingBackend`（哈希向量，仅供开发测试，无真实语义）。

生产环境推荐：`ChromaVectorBackend` + `SentenceTransformerBackend`。

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
# 结果: 46/46 通过
```

---

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

---

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
