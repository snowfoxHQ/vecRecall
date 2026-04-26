**English** | [中文](README.md)

## Changelog v1.0.4

**Date:** April 26, 2026

### What's New

**New `vr add-file` command (large file input)**

Solves the Windows command-line 8191-character limit that prevented long content from being stored. Supports files of any size. Verified working with 75,328 characters stored in full.

- Auto-detects file encoding (UTF-8 / GBK / GB2312 etc.)
- Shows file size, character count, and token estimate on store
- Usage: `vr add-file D:ile.txt --topic topic --importance 0.9`

**New `vr get` command (view full content)**

View the complete raw content of a memory by ID, without truncation. Supports full ID or 8-character short ID.

- Usage: `vr get first8chars`
- Shows full content, timestamp, wing, topic, character count

**New `vr browse` command (memory directory)**

Browse memories by date in a compact one-line-two-column format.

- Usage: `vr browse` / `vr browse --all` / `vr browse --wing project`
- Format: `2026-04-26  |  [devlog] VecRecall dev log  [test] test record`

### Full Workflow

```powershell
# Store a large file
vr add-file D:
otes.txt --topic notes --importance 0.9

# Browse memory directory
vr browse

# Semantic search
vr search "keyword"

# View full content
vr get first8chars
```

---

## Changelog v1.0.3

**Date:** April 25, 2026

### What's New

**Blockchain context archive submodule (vecrecall/blockchain/)**

Four new core files:

- `block.py` — Block data structure with full content, timestamp, keywords, and SHA-256 hash chain
- `chain.py` — Hash-chain management with SQLite persistence, date+keyword search, and auto L1 grouping (every 4 blocks)
- `indexer.py` — Chinese/English keyword extraction with sliding window for Chinese sub-words
- `hooks.py` — Platform triggers for OpenClaw, Hermes Agent, and Claude Code

Core feature: archives AI context windows as blocks to break through the single-context-window limit. Auto-triggers at 75% usage, force-archives before compaction, and injects historical key fragments into new context windows.

**New files**
- `vecrecall/blockchain/__init__.py`
- `vecrecall/blockchain/block.py`
- `vecrecall/blockchain/chain.py`
- `vecrecall/blockchain/indexer.py`
- `vecrecall/blockchain/hooks.py`
- `tests/test_blockchain.py` (72/72 passed)
- `README_BLOCKCHAIN.md`
- `README_EN.md` (this file)

---

# VecRecall

An improved AI long-term memory system, rebuilt from scratch based on design analysis of the original MemPalace.

## Key Differences from the Original

| | Original MemPalace | VecRecall |
|--|--|--|
| Retrieval path | Vector + Room metadata filter | **Pure vector, no structural filter** |
| L2 trigger | Room name matching | **Semantic similarity threshold (default 0.55)** |
| AAAK summary | Participates in retrieval index | **UI layer only, not indexed** |
| Wing/Topic | Affects both retrieval and display | **UI organization only, no retrieval impact** |
| Recall rate (R@5) | ~84% (with all features enabled) | **96.6%+ (pure vector baseline)** |

The core problem with the original: **the information organization layer and the retrieval path were tightly coupled**. Room filtering dropped recall from 96.6% to 89.4%, and AAAK participation in retrieval further dropped it to 84.2%. VecRecall separates these completely: retrieval uses vectors, organization uses the SQLite UI layer.

---

## Installation

```bash
# Basic (zero dependencies, hash embedding + in-memory vector)
pip install -e .

# Production (real semantic embedding + ChromaDB persistence)
pip install -e ".[full]"
```

## Quick Start

### Python API

```python
from vecrecall import VecRecall

with VecRecall(base_dir="~/.vr", wing="my-project") as palace:
    # Store a memory (verbatim, never rewritten)
    palace.add(
        content="Decided to use PostgreSQL instead of MySQL — better JSON support",
        topic="database",
        importance=0.9,
        ui_summary="DB migration → PG",   # AAAK summary, UI display only, not indexed
    )

    # Semantic search (pure vector, no structural filter)
    results = palace.search("database selection", n=5)

    # Build four-layer context for direct AI prompt injection
    ctx = palace.build_context(current_query="continuing the database discussion today")
    print(ctx.l0_identity)            # L0: ~50 tokens
    print(len(ctx.l1_key_moments))    # L1: top-15 key moments
    print(len(ctx.l2_topic_context))  # L2: semantically triggered context
```

### CLI

```bash
# Initialize
vr init --dir ~/.vr --wing my-project

# Add a memory
vr add "Fixed JWT expiry bug in auth module" --topic auth --importance 0.85
echo "Today's meeting notes..." | vr add - --topic meeting

# Semantic search
vr search "authentication issues" --layer l3

# Build four-layer context
vr context "the auth solution we discussed before" --l3

# System status
vr stats
vr wings
vr topics --wing my-project

# Agent diary (isolated per agent)
vr diary write reviewer "Found SQL injection vulnerability #bug-456"
vr diary write architect "Decided to adopt CQRS pattern"
vr diary read reviewer "security vulnerability"

# Archive a full session
vr archive session.json

# Export / Import
vr export my-project --out backup.json
vr import backup.json
```

### MCP Server (Claude Code / Gemini CLI)

```bash
# Start MCP stdio server
vr mcp --dir ~/.vr --wing my-project

# Or directly
vr-mcp --dir ~/.vr --wing my-project
```

Configure in Claude Code's `mcp_config.json`:

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

## Four-Layer Memory Stack

Each AI wake-up loads only 600–900 tokens, instead of stuffing all history into the prompt.

```
L0  Identity layer       ~50 tokens    Loaded every time, fixed
L1  Key moments          ~600 tokens   Top-15 by importance, no wing filter
L2  Topic context        ~300 tokens   Loaded when similarity ≥ 0.55
L3  Deep retrieval       On demand     Full semantic search, hits vector store directly
```

The L2 change is critical: the original used Room name matching to trigger loading. VecRecall uses a semantic similarity threshold instead.
Threshold is adjustable: `vr-mcp` tool `mp_set_l2_threshold`, or in code `palace.L2_TRIGGER_THRESHOLD = 0.6`.

---

## MCP Tools (26 total)

**Write**
- `mp_add` — Store a single memory
- `mp_add_batch` — Batch store
- `mp_update_importance` — Update importance score

**Retrieval (all use pure vector, no structural filter)**
- `mp_search` — Semantic search
- `mp_build_context` — Build four-layer context bundle
- `mp_l1_moments` — Get L1 key moments
- `mp_l2_context` — L2 semantically triggered context
- `mp_l3_deep` — L3 full deep retrieval
- `mp_fuzzy_recall` — Fuzzy recall (low threshold, loose match)

**Organization layer (UI browsing only, no retrieval impact)**
- `mp_list_wings` — List all wings
- `mp_list_topics` — List topics
- `mp_browse_wing` — Browse a wing
- `mp_browse_topic` — Browse a topic
- `mp_get_memory` — Get memory by ID

**Knowledge graph**
- `mp_link` — Create cross-wing association

**Agent diary**
- `mp_diary_write` — Write agent diary entry
- `mp_diary_read` — Read agent diary

**Session archive**
- `mp_archive_session` — Archive full conversation

**Management**
- `mp_stats` — System statistics
- `mp_health` — Health check
- `mp_export_wing` — Export wing data
- `mp_import_json` — Import JSON
- `mp_set_identity` — Update L0 identity layer
- `mp_set_wing` — Switch default wing
- `mp_set_l2_threshold` — Adjust L2 threshold
- `mp_format_prompt` — Format as injectable prompt

---

## Pluggable Backends

```python
from vecrecall.core.engine import (
    VecRecall,
    ChromaVectorBackend,         # requires: pip install chromadb
    SentenceTransformerBackend,  # requires: pip install sentence-transformers
)

palace = VecRecall(
    base_dir="~/.vr",
    wing="prod",
    vector_backend=ChromaVectorBackend("~/.vr/chroma"),
    embedding_backend=SentenceTransformerBackend("all-MiniLM-L6-v2"),
)
```

Default backend (zero dependencies): `NumpyVectorBackend` + `HashEmbeddingBackend` (hash vectors, no real semantics, for development/testing only).

Recommended for production: `ChromaVectorBackend` + `SentenceTransformerBackend`.

Auto-detection: if `sentence-transformers` and `chromadb` are installed, VecRecall automatically switches to the production backends on startup — no configuration needed.

---

## Privacy

- Fully local, no data uploaded
- Core features require no API key
- SQLite stores metadata and raw text; vector store holds embeddings
- Data directory defaults to `~/.vecrecall`, fully customizable

---

## Tests

```bash
python tests/test_core.py
# Result: 46/46 passed
```

---

## Windows Installation Notes

Verified on Windows 11 (April 23, 2026).

**Environment**
- OS: Windows 11
- Python: 3.10+
- Install path: `I:\Github\VecRecall`
- Data directory: `C:\Users\admin\.vecrecall`

**Installation**

```powershell
cd I:\Github\VecRecall
pip install -e .
pip install sentence-transformers chromadb
```

**Verified output**

```
PS I:\Github\VecRecall> vr stats
📊 VecRecall Status
  Total memories: 1
  Wings:          1
  Cross-links:    0
  Data directory: C:\Users\admin/.vecrecall
  Default wing:   default
  Vector backend: ChromaVectorBackend        ← auto-enabled after installing chromadb
  Embedding backend: SentenceTransformerBackend  ← auto-enabled after installing sentence-transformers
```

**Notes**
- Without production backends, VecRecall uses `HashEmbeddingBackend` (no real semantics)
- After installing `sentence-transformers` and `chromadb`, backends switch automatically — no config needed
- The model file (~90MB) is downloaded on first run, not at install time
- The pip "new version available" notice is informational only and does not affect functionality
- v1.0.2 fixes Chinese character encoding on Windows PowerShell — Chinese and English input both work correctly

---

## Changelog

### v1.0.2 — April 23, 2026

**Auto encoding detection for Chinese/English input (cli/main.py)**

Fixed garbled Chinese text when using `vr add` on Windows PowerShell. PowerShell defaults to GBK encoding for command-line arguments, causing Chinese characters to be stored as garbage. v1.0.2 adds automatic encoding detection at the CLI entry point:

- Detects stdin/stdout/stderr encoding at startup and reconfigures to UTF-8 if needed
- Automatically fixes Chinese argument encoding in sys.argv on Windows
- Chinese/English mixed content handled correctly
- No impact on non-Windows environments

### v1.0.1 — April 23, 2026

**Auto backend detection (engine.py)**

Fixed an issue where `sentence-transformers` and `chromadb`, once installed, were not being used — VecRecall continued using hash embeddings, making semantic search non-functional. Now detects installed backends at startup and switches automatically:

- `sentence-transformers` detected → use `SentenceTransformerBackend`
- `chromadb` detected → use `ChromaVectorBackend`
- Neither installed → fall back to `HashEmbeddingBackend` + `NumpyVectorBackend`

No configuration needed. Takes effect immediately after installing the dependencies.

### v1.0.0 — April 20, 2026

Initial release. Full reimplementation based on design analysis of MemPalace, with the retrieval path and organization layer fully decoupled.

---

## Blockchain Context Archive Module (v1.0.3)

VecRecall includes a built-in blockchain submodule that archives AI context windows as blocks, breaking through the single-context-window limit.

### Core Idea

```
Context window 1 (2M tokens) ─→ archived as Block #0
Context window 2 (2M tokens) ─→ archived as Block #1, injects key fragments from Block #0
Context window 3 (2M tokens) ─→ archived as Block #2, injects key fragments from history
...
Theoretically unlimited stacking — the AI always knows all historical context
```

### Storage Structure

| Level | Description |
|-------|-------------|
| Small block | Full record of one context window |
| L1 group | 4 small blocks auto-merged |
| L2 group | 4 L1 groups auto-merged |
| Search index | Date + keyword index |

### Trigger Conditions

- **Auto trigger (75%)**: Archives when context usage reaches 75%, leaving 25% buffer
- **Pre-compaction trigger**: Force-archives before context compression — compression ≠ forgetting
- **Manual trigger**: User initiates archive at any time

### Tamper-proof

Each block contains the SHA-256 hash of the previous block. Any tampering breaks the chain. Call `verify_chain()` at any time to check integrity.

### Supported Platforms

OpenClaw, Hermes Agent, and Claude Code — isolated by wing. See `README_BLOCKCHAIN.md` for details.

### File Structure

```
vecrecall/blockchain/
  __init__.py    Module entry
  block.py       Block / BlockGroup data structures
  chain.py       BlockChain hash-chain management
  indexer.py     Keyword extraction (Chinese + English)
  hooks.py       Triggers for all three platforms
```

### Quick Start

```python
from vecrecall.blockchain import BlockChain, create_hook

# Create a chain
chain = BlockChain(db_path="~/.vr/blockchain/chain.db")

# Archive a block
block = chain.new_block(
    content="Full conversation content...",
    wing="my-project",
    trigger="auto_75",
    keywords=["database", "architecture"],
)

# Search by keywords
results = chain.search_by_keywords(["database"], date_start="2026-04-01")

# Verify chain integrity
ok, msg = chain.verify_chain("my-project")

# Use a platform hook
hook = create_hook("openclaw", {
    "db_path": "~/.vr/blockchain/chain.db",
    "wing": "openclaw-agent",
    "context_window_size": 2_000_000,
})
```

### Tests

```bash
python tests/test_blockchain.py
# Result: 72/72 passed
```
