[中文](README.md) | **English**

# VecRecall v2.0

A cognitive memory system. Rebuilt from a design analysis of the original MemPalace, v2.0 adds an **LLM cognitive layer** (automatic entity / relation / summary / topic extraction on write + semantic dedup & merge), a **temporal knowledge graph**, **forgetting curves & active distillation**, **multi-hop vector+graph hybrid retrieval**, **at-rest encryption**, and a **semantic embedding upgrade** (subword-augmented lexical + multilingual semantic model).

## Key Differences from the Original

| | Original MemPalace | VecRecall |
|--|--|--|
| Retrieval path | Vector + Room metadata filter | **Pure vector, no structural filter** |
| L2 trigger | Room name matching | **Semantic similarity threshold (default 0.55)** |
| AAAK summary | Participates in retrieval index | **UI layer only, not indexed** |
| Wing/Topic | Affects both retrieval and display | **UI organization only, no retrieval impact** |
| Recall rate (R@5) | ~84% (with all features enabled) | **Semantic embedding 100% / zero-dependency lexical 76.2% (built-in eval set)** |

The original's core problem: **the information organization layer and the retrieval path were tightly coupled**. Room filtering dropped recall from 96.6% to 89.4%, and AAAK participation in retrieval further dropped it to 84.2%. VecRecall separates the two completely: retrieval uses vectors, organization uses the SQLite UI layer.

---

## Installation

```bash
# Basic (zero dependencies: lexical semantic embedding + SQLite-persisted vectors)
pip install -e .

# Production (real semantic embedding + ChromaDB persistence)
pip install -e ".[full]"

# At-rest encryption (encrypt memory content on disk; requires cryptography)
pip install -e ".[crypto]"
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
# Initialize (--chain enables the tamper-proof hash chain; --embedder selects the embedding backend)
vr init --dir ~/.vr --wing my-project --chain
vr init --embedder ollama --embed-model nomic-embed-text   # Local Ollama (no API key)
vr init --embedder api --embed-model text-embedding-3-small # Remote API (reads OPENAI_API_KEY)

# Add a memory
vr add "Fixed JWT expiry bug in auth module" --topic auth --importance 0.85
echo "Today's meeting notes..." | vr add - --topic meeting

# Semantic search (--hybrid re-ranks with importance + recency)
vr search "authentication issues" --layer l3 --hybrid

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

# Memory lifecycle management
vr delete <memory-id>                 # Delete (supports 8-char short ID)
vr prune --min-importance 0.3 --older-than 90  # Prune low-value memories (preview; add --yes to confirm)
vr reindex                            # Rebuild vector index from raw content

# List memories (structured listing for browsing / selection / citation)
vr list --wing my-project --limit 20
vr list --topic auth --full           # Include raw content

# Tamper-proof verification (requires --chain enabled)
vr verify

# Archive a full session
vr archive session.json

# Export / Import
vr export my-project --out backup.json
vr import backup.json
```

## Cognitive Layer (LLM Extraction + Semantic Dedup)

New in v2.0: on write, a pluggable `MemoryExtractor` automatically extracts structured information, and highly similar content is dedup-merged to prevent the memory store from bloating.

### Extractors (MemoryExtractor)

| Extractor | Dependency | Description |
|---|---|---|
| `heuristic` | Zero | Keyword topics + heuristic importance + English proper-noun entities |
| `ollama` | Local Ollama | Calls `/api/chat` to let an LLM extract entities / relations / summary / topic / importance |
| `api` | Remote API | Calls an OpenAI-compatible `/chat/completions` endpoint |

```bash
# Enable the LLM cognitive layer (local Ollama, no API key)
vr init --extractor ollama --llm-model llama3.2

# Or a remote OpenAI-compatible API
vr init --extractor api --llm-model gpt-4o-mini --llm-api-key $LLM_KEY

# All subsequent `vr add` calls auto-extract topic / importance / summary / entities / relations
vr add "Alice joined the microservices team, responsible for Kubernetes deployment"

# Or override temporarily (without writing config)
vr add "..." --extractor ollama --llm-model llama3.2
```

Extracted results auto-populate the memory's `topic`, `importance`, and `ui_summary`; entities and relations are stored in `metadata.entities` / `metadata.relations` (reserved for the temporal knowledge graph). If the LLM call fails, it gracefully degrades to heuristics — writes are never blocked.

### Semantic Dedup & Merge

Enabled by default: before writing, a vector search is performed; if similarity to an existing memory is ≥ `dedup_threshold` (default 0.92), it merges (`occurrences + 1`, importance takes the max) instead of adding a new one.

```python
m1 = palace.add(content="Decided to adopt microservices architecture")
m2 = palace.add(content="Decided to adopt microservices architecture")   # → merged, returns m1
assert m1.id == m2.id
assert m2.metadata["occurrences"] == 2

palace.add(content="Decided to adopt microservices architecture", dedup=False)  # Explicitly disable dedup
```

You can also construct it directly on the Python side:

```python
from vecrecall import VecRecall, LLMExtractor

with VecRecall(base_dir="~/.vr", wing="my-project",
               extractor=LLMExtractor(provider="ollama", model="llama3.2"),
               dedup_threshold=0.92) as palace:
    palace.add("Alice joined the microservices team")
```

### Temporal Knowledge Graph

Entities, relations, and states extracted by the cognitive layer flow into a **timestamped knowledge graph** (`graph.db`), recording entity appearances, entity-to-entity associations, and the evolution of entity attributes over time.

```python
palace.add("Alice joined the microservices team")   # state: Alice.team = microservices team
palace.add("Alice transferred to the platform team") # state: Alice.team = platform team (new timeline entry)

alice = palace.entity("Alice")
print(alice["attributes"])      # {'team': 'platform team'}  ← current snapshot
for s in alice["timeline"]:     # state evolution, ascending by time
    print(s["attribute"], s["value"], s["timestamp"])
for r in alice["relations"]:    # directly connected relations
    print(r["source"], r["type"], r["target"])

palace.list_entities()          # descending by mention count
palace.graph_stats()            # {'entities': N, 'entity_states': M, 'relations': K}
```

CLI queries:

```bash
vr graph              # List entities (by mention count)
vr entity Alice       # View Alice's profile + state evolution + associated relations
```

> With zero dependencies (`HeuristicExtractor`), the graph auto-registers English proper-noun entities and same-sentence co-occurrence relations; with an LLM extractor (`--extractor ollama/api`) you additionally get Chinese entities, semantic relations, and entity attribute states — a true "who was in what state when".

### Forgetting Curve & Active Distillation

Memory strength decays exponentially following the **Ebbinghaus forgetting curve**: higher importance decays slower, and reviewed memories decay slower. Memories whose strength drops below a threshold enter the "forgotten" state and can be pruned or compressed.

```python
palace.memory_strength(mem.id)   # Current strength 0–1 (new memory ≈ 1)
palace.forgotten(0.2)            # Forgotten memories (strength < 0.2)
palace.recall(mem.id)            # Review once: spacing effect strengthens memory

# Active distillation: compress fragmented low-value memories under one topic into a single abstract long-term memory
plans = palace.distill(topic="diary", dry_run=True)    # Preview the plan first
palace.distill(topic="diary", dry_run=False)           # Actually execute
```

- Distilled memories carry `metadata.distilled=True` + `sources=[...]`; source memories are tagged `distilled_into`.
- Without an LLM, summaries are stitched heuristically; with an `LLMExtractor`, an LLM generates a condensed summary.
- `prune(use_forgetting=True)` prunes by the forgetting curve (important memories decay slower, naturally harder to delete by accident).

CLI:

```bash
vr decay                       # Forgetting assessment
vr distill --topic diary       # Active distillation (dry-run)
vr distill --topic diary --yes # Actually execute
```

### Multi-Hop Vector+Graph Hybrid Retrieval

On top of vector semantic retrieval, add the knowledge graph's **multi-hop relation expansion**: vector-recalled seeds → expand associated entities outward along the entity relation graph → summarize explainable evidence paths.

```python
res = palace.graph_search("Alice", hops=2)
res["direct"]     # Direct vector hits
res["graph"]      # Associated memories discovered via graph expansion (with entities / paths)
# paths example: Alice -co-occurs-> Google -co-occurs-> DeepMind
```

```bash
vr graph-search "Alice" --hops 2
```

### Semantic Embedding Upgrade

v2.0's embedding has two tiers; recall rates (R@5) are in `benchmarks/benchmark_recall.py` (built-in 21 mixed Chinese/English queries + 15 distractor documents, reproducible):

| Embedding backend | R@5 | Dependency |
|--|--|--|
| Lexical (`bow`, default, zero-dependency) | 76.2% | None |
| Semantic (`sentence`, multilingual MiniLM) | 100% | `pip install -e ".[sentence]"` |

- **Zero-dependency lexical enhancement**: `BagOfWordsEmbeddingBackend` was upgraded from "whole word + Chinese bigrams" to "whole word + English character 3/4-grams (subword) + Chinese trigrams", capturing word-form variants (running/run) and spelling variants (database/databases) with no external dependency.
- **Multilingual semantic model**: the `sentence` / `auto` default model was upgraded from English `all-MiniLM-L6-v2` to multilingual `paraphrase-multilingual-MiniLM-L12-v2` (Chinese-friendly), overridable via `--embed-model`.
- The eval set deliberately includes semantically-equivalent paraphrases with zero lexical overlap (e.g. "程序崩溃了怎么办" → "应用 crash 时的排查步骤"), so 76.2% is the bare floor without a semantic model; plugging in a semantic model reaches 100%. The 96.6% claimed in v1 was a reproduction of the "original's pure-vector ceiling" (old eval set, no script); from v2 onward it is re-quantified with a reproducible benchmark.

```bash
python benchmarks/benchmark_recall.py          # Lexical
python benchmarks/benchmark_recall.py --all    # Lexical + semantic
```

---

### At-Rest Encryption

Memory content is encrypted on disk, preventing plaintext leakage if the database file is read directly. Encryption scope: raw content, AAAK summaries, and metadata in `knowledge.db` (the vector index holds non-plaintext embeddings, the graph is derived structure).

```python
from vecrecall import VecRecall, Cipher

key = Cipher.generate_key()          # Generate a random key (keep it safe!)
palace = VecRecall(base_dir="~/.vr", encryption_key=key)
palace.add("database password is hunter2")    # content / summary / metadata encrypted on disk
```

- Key source: provide a Fernet key directly (44-char urlsafe base64), or a password (PBKDF2-derived, salt persisted in `salt.key`).
- Reading with the wrong key raises `InvalidToken` (never silently returns ciphertext); existing plaintext legacy data is automatically compatible.
- Requires `cryptography` (`pip install vecrecall[crypto]`); if not installed, enabling encryption gives a clear message — never silently degrades to plaintext.

CLI:

```bash
vr init --encrypt                    # Generate a key and write it to config
vr init --encrypt-key "your password" # Or specify a key/password
VR_ENCRYPTION_KEY="..." vr add "..."  # Runtime override via env var (applies to all commands)
```

---

### MCP Server (Claude Code / Gemini CLI)

```bash
# Start the MCP stdio server (--embedder selects ollama / api)
vr mcp --dir ~/.vr --wing my-project --embedder ollama

# Or directly
vr-mcp --dir ~/.vr --wing my-project --embedder ollama --embed-model nomic-embed-text
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

### HTTP REST API Server (for traditional software)

Besides MCP, a zero-dependency HTTP JSON interface is built in — any language (curl / requests / Java / C# / Node / Go ...) can integrate:

```bash
# Start the REST API server (default http://127.0.0.1:8791)
# In production, always enable auth with --api-key (or the VR_API_KEY env var)
vr api --dir ~/.vr --wing my-project --embedder ollama --api-key my-secret-token

# Or directly
vr-api --dir ~/.vr --port 8791 --api-key my-secret-token
```

**Authentication**: when `--api-key` (or `VR_API_KEY`) is configured, all requests except `/health` must carry the token — request header `Authorization: Bearer <token>` or query parameter `?key=<token>`; otherwise 401. Binding to a non-loopback address (e.g. `--host 0.0.0.0`) **requires** a token.

Common endpoints:

```bash
TOKEN=my-secret-token
BASE=http://127.0.0.1:8791

curl $BASE/health                                    # Health check (no auth)
curl -H "Authorization: Bearer $TOKEN" $BASE/memories      # List memories
curl -H "Authorization: Bearer $TOKEN" "$BASE/memories?topic=auth&full=1"
curl -H "Authorization: Bearer $TOKEN" $BASE/memories/<id>  # Single memory

curl -X POST $BASE/memories \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content": "Decided to adopt microservices architecture", "topic": "arch", "importance": 0.9}'

curl -X POST $BASE/search \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "architecture decision", "n": 5, "hybrid": true}'

curl -X POST $BASE/context \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"current_query": "continue discussing architecture", "load_l2": true}'

curl -X DELETE -H "Authorization: Bearer $TOKEN" $BASE/memories/<id>  # Delete
```

Full endpoints: `GET /stats` `/wings` `/topics`, `POST /memories/batch` `/prune` `/reindex` `/verify`, `PUT /memories/{id}`. All responses are JSON.

---

## Security

VecRecall provides multiple layers of protection for interface access:

| Protection | Mechanism |
|---|---|
| **REST auth** | `--api-key` / `VR_API_KEY` enables Bearer Token (or `?key=`), otherwise 401; binding to a non-loopback address requires a token |
| **CORS hardening** | No CORS headers sent by default — browser pages cannot cross-origin read; use `--cors-origin` to explicitly allow a specific Origin |
| **File path isolation** | MCP's `mp_import_json` / `mp_export_wing` restrict paths to the data directory, preventing arbitrary file read/write (including prompt-injection attempts) |
| **SQL injection protection** | All queries are parameterized (`?` placeholders), never concatenating user input |
| **Tamper-proof (integrity)** | `--chain` hash chain: each write generates a block; `mp_verify` / `vr verify` detect content tampering |
| **Key never stored on disk** | The remote embedding API key is read only from environment variables, never written to config.json |

**Note**: memory content is stored in plaintext (no at-rest encryption) unless encryption is enabled; for sensitive data, pair with disk encryption or self-hosting.

---

## Four-Layer Memory Stack

Each AI wake-up loads only 600–900 tokens, instead of stuffing all history into the prompt.

```
L0  Identity layer       ~50 tokens    Loaded every time, fixed
L1  Key moments          ~600 tokens   Top-15 by importance, no wing filter
L2  Semantic context     ~300 tokens   Loaded when similarity to current dialogue ≥ 0.55
L3  Deep retrieval       On demand     Full semantic search, hits vector store directly
```

The L2 change is critical: the original used Room name matching to trigger loading. VecRecall uses a semantic similarity threshold instead.
The threshold is adjustable: `vr-mcp` tool `mp_set_l2_threshold`, or in code `palace.L2_TRIGGER_THRESHOLD = 0.6`.

---

## MCP Tools (32 total)

**Write**
- `mp_add` — Store a single memory
- `mp_add_batch` — Batch store
- `mp_update` — Update memory content / topic / importance / summary (content changes auto re-vectorize)
- `mp_update_importance` — Update importance score
- `mp_delete` — Delete a memory
- `mp_prune` — Prune low-value memories
- `mp_reindex` — Rebuild all vector indexes from raw content

**Retrieval (all pure vector, no structural filter)**
- `mp_search` — Semantic search
- `mp_build_context` — Build four-layer context bundle
- `mp_l1_moments` — Get L1 key moments
- `mp_l2_context` — L2 semantically triggered context
- `mp_l3_deep` — L3 full deep retrieval
- `mp_fuzzy_recall` — Fuzzy recall (low threshold, loose match)

**Organization layer (UI browsing only, no retrieval impact)**
- `mp_list_wings` — List all wings
- `mp_list_topics` — List topics
- `mp_list_memories` — List memories (structured listing with ID / summary / importance / preview, optional raw content)
- `mp_browse_wing` — Browse a wing
- `mp_browse_topic` — Browse a topic
- `mp_get_memory` — Get memory by ID

**Knowledge graph**
- `mp_link` — Create cross-wing association

**Agent diary**
- `mp_diary_write` — Write agent diary entry
- `mp_diary_read` — Read agent diary

**Session archive**
- `mp_archive_session` — Archive a full conversation

**Management**
- `mp_stats` — System statistics
- `mp_health` — Health check
- `mp_verify` — Verify hash chain integrity (tamper-proof)
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
    ChromaVectorBackend,      # requires: pip install chromadb
    SentenceTransformerBackend,  # requires: pip install sentence-transformers
)

palace = VecRecall(
    base_dir="~/.vr",
    wing="prod",
    vector_backend=ChromaVectorBackend("~/.vr/chroma"),
    embedding_backend=SentenceTransformerBackend(),  # default multilingual MiniLM; custom model name accepted
    use_blockchain=True,   # optional: generate hash-chain blocks on write for tamper-proof audit
)
```

**Dual embedding backend selection (local Ollama / remote API)**, constructed uniformly via the `build_embedding_backend` factory:

```python
from vecrecall import VecRecall, build_embedding_backend

# Local Ollama (no API key, data stays on-machine; nomic-embed-text / bge-m3 recommended)
emb = build_embedding_backend("ollama", model="nomic-embed-text")

# Remote API (OpenAI-compatible; api_key defaults to env var OPENAI_API_KEY)
emb = build_embedding_backend(
    "api", model="text-embedding-3-small",
    base_url="https://api.openai.com/v1",
)

palace = VecRecall(base_dir="~/.vr", embedding_backend=emb)
```

Factory values: `auto` (default, uses sentence-transformers if available, otherwise lexical) / `bow` (zero-dependency lexical) / `sentence` (local model) / `ollama` (local Ollama) / `api` (OpenAI-compatible remote). CLI, MCP, and REST servers all select via `--embedder`.

Default backends (zero dependency): `SqliteVectorBackend` (SQLite-persisted vectors, index survives restart) + `BagOfWordsEmbeddingBackend` (feature-hash semantic embedding: Chinese single chars + bigrams + trigrams, English character 3/4-gram subwords, capturing word-form / spelling variants). The default install gives cross-session persistence and zero-dependency semantic recall with no external dependency.

Recommended for production: `ChromaVectorBackend` + `SentenceTransformerBackend` (deeper semantics).

After changing the embedding backend, run `vr reindex` (or `mp_reindex`) once to rebuild all vector indexes from raw content and migrate existing data.

---

## Privacy

- Fully local, no data uploaded
- Core features require no API key
- SQLite stores metadata and raw text; the vector store holds embeddings
- Data directory defaults to `~/.vecrecall`, fully customizable

---

## Tests

```bash
python tests/test_core.py
# Result: 158/158 passed

python tests/test_blockchain.py
# Result: 72/72 passed
```

---

## v2.0 Upgrade Notes

v2.0 completes the "cognitive layer", upgrading from a passive RAG memory store to an active, structured memory system:

| Upgrade | Description |
|---|---|
| **LLM memory extractor** | New `MemoryExtractor` abstraction + `HeuristicExtractor` (zero-dependency) / `LLMExtractor` (Ollama / OpenAI-compatible) implementations, auto-extracting topic, importance, summary, entities, relations, entity state on write, with graceful degradation on failure |
| **Semantic dedup & merge** | `add()` dedups by default: similarity ≥ 0.92 merges into the existing memory (`occurrences+1`), avoiding duplicate bloat; disable with `dedup=False` |
| **Temporal knowledge graph** | New `TemporalGraph` (`graph.db`): entity nodes + state timelines + timestamped relations; `entity()` / `entity_timeline()` / `entity_relations()` / `list_entities()` trace "who was in what state when" |
| **Forgetting curve & distillation** | New `ForgettingCurve` (Ebbinghaus exponential decay, corrected by importance + review spacing effect): `memory_strength()` / `forgotten()` / `recall()`; `distill()` actively distills fragmented memories into abstract long-term memory; `prune(use_forgetting=True)` prunes by the forgetting curve |
| **Multi-hop hybrid retrieval** | New `graph_search()`: vector-recalled seeds → BFS expansion of associated entities along the entity relation graph → returns associated memories with evidence paths; `TemporalGraph` adds entity-memory mapping and `neighbors()` reverse lookup |
| **Interface security hardening** | REST adds Bearer Token auth (`--api-key` / `VR_API_KEY`) + tightened CORS (cross-origin denied by default); MCP file path isolation prevents arbitrary read/write; SQL parameterization hardened |
| **At-rest encryption** | New `vecrecall/core/crypto.py` (`Cipher`, Fernet authenticated encryption + PBKDF2 password derivation): the `encryption_key` parameter encrypts `knowledge.db`'s content / summary / metadata on disk, wrong keys are rejected on read, plaintext legacy data is compatible; enable via `vr init --encrypt` / `VR_ENCRYPTION_KEY` |
| **Semantic embedding upgrade** | Lexical embedding adds subword enhancement (English character 3/4-gram + Chinese trigrams), capturing word-form / spelling variants; the `sentence` / `auto` default model upgrades to multilingual `paraphrase-multilingual-MiniLM-L12-v2`; new `benchmarks/benchmark_recall.py` reproducible recall evaluation (lexical 76.2% / semantic 100%) |

New CLI options: `--extractor` / `--llm-model` / `--llm-url` / `--llm-api-key` (persisted by `vr init`, temporarily overridable by `vr add` / `vr mcp` / `vr api`), `--encrypt` / `--encrypt-key` (at-rest encryption). New query commands: `vr graph` (list entities) / `vr entity NAME` (entity profile & state evolution) / `vr decay` (forgetting assessment) / `vr distill` (active distillation) / `vr graph-search QUERY` (multi-hop hybrid retrieval).

---

## v1.1 Upgrade Notes

Upgrades completed from the architecture-gap analysis (relative to v1.0.x):

| Upgrade | Description |
|--|--|
| **Persistent vector backend** | The original default `NumpyVectorBackend` was in-memory only — all retrieval failed after restart; new `SqliteVectorBackend` is the zero-dependency default, storing vectors as binary BLOBs in SQLite |
| **Lexical semantic embedding** | The original `HashEmbeddingBackend` (whole-text single hash, no semantics) is replaced by `BagOfWordsEmbeddingBackend` (feature hashing + Chinese single chars / bigrams), giving real lexical-level semantics out of the box |
| **Memory lifecycle** | New `update` / `delete` / `prune` / `reindex`; the vector index and SQLite stay in sync, closing the "can only add, cannot modify/delete" gap |
| **Hybrid scoring retrieval** | `search`/`build_context` support `hybrid=True`: score = 0.6·similarity + 0.25·importance + 0.15·recency (opt-in, does not change the pure-vector recall set) |
| **Blockchain integration** | The formerly standalone `blockchain/` module is wired into the core engine: with `use_blockchain=True`, writes synchronously generate tamper-proof blocks; `verify_integrity()` detects content tampering |
| **Chinese token estimation** | `estimate_tokens()` distinguishes Chinese/English (Chinese ≈ 0.7 token/char, others ≈ 4 chars/token) |
| **Dual embedding backend selection** | New `OllamaEmbeddingBackend` (local Ollama, no key) and `APIEmbeddingBackend` (OpenAI-compatible remote), with the `build_embedding_backend()` factory; CLI / MCP / REST all select via `--embedder` |
| **HTTP REST API** | New `vr api` / `vr-api`: a zero-dependency `http.server` JSON interface for traditional software (curl / any language), with CORS |
| **Memory listing** | New `VecRecall.list_memories()` + MCP `mp_list_memories` + REST `GET /memories` + CLI `vr list`: paginated / filtered / sorted structured memory listing with optional raw content |

New CLI subcommands: `vr delete`, `vr prune`, `vr verify`, `vr reindex`, `vr list`, `vr api`; new MCP tools: `mp_update`, `mp_delete`, `mp_prune`, `mp_reindex`, `mp_verify`, `mp_list_memories`.
