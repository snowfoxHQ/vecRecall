[中文](README.md) | **English**

# VecRecall v2.2

> Full release history: [CHANGELOG.md](CHANGELOG.md).

A cognitive memory system that also works as a memory runtime for multi-agent setups. Rebuilt from a design analysis of the original MemPalace, v2.0 adds an **LLM cognitive layer** (automatic entity / relation / summary / topic extraction on write + semantic dedup & merge), a **temporal knowledge graph**, **forgetting curves & active distillation**, **multi-hop vector+graph hybrid retrieval**, **at-rest encryption**, and a **semantic embedding upgrade** (subword-augmented lexical + multilingual semantic model).

**v2.2 adds runtime primitives** (all optional with defaults; single-agent usage is unchanged):

- **Typed memory**: `memory_type` = episodic / semantic / procedural. Semantic memories always enter the temporal graph; procedural memories (rules, thresholds, policies) are matched exactly by scenario tags + version, not by fuzzy vector recall.
- **Isolation**: `wing` × `visibility` (private/shared) × `owner`. Private memories are visible only to their writer, shared ones to the whole wing; cross-wing links are allowed only between shared memories.
- **Immutable memory & per-key chains**: memories with `retention_policy="immutable"` skip dedup, forgetting, pruning and distillation and are always loaded into L1; the hash chain is split by `topic_key`, so one topic's evidence can be verified on its own.

## Key Differences from the Original

| | Original MemPalace | VecRecall |
|--|--|--|
| Retrieval path | Vector + Room metadata filter | **Pure vector, no structural filter** |
| L2 trigger | Room name matching | **Semantic similarity threshold (default 0.55)** |
| AAAK summary | Participates in retrieval index | **UI layer only, not indexed** |
| Wing/Topic | Affects both retrieval and display | **UI organization only, no retrieval impact** |
| Recall | ~84% R@5 (all features enabled, self-reported by the original) | **LongMemEval-S session-level R_any@5 75.3% (zero-dependency lexical embedding, 470 questions, reproducible)** |

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

    # Typed writes (v2.2)
    palace.add("Vendor Acme added to the approved list", memory_type="semantic")
    palace.add("Payments over 50k need two approvers", memory_type="procedural",
               scenario_tags=["payment", "gatekeeping"], policy_version="v1")
    palace.query_procedural(scenario_tags=["gatekeeping"], version="v1")

    # Immutable memory: no dedup, no forgetting, never pruned; own chain per topic_key
    palace.add("Release decision: freeze the retrieval API for v2.2",
               retention_policy="immutable", topic_key="release:v2.2")
```

### Multi-Agent Example (v2.2)

```python
# Two agents, each with its own instance, sharing one data directory
agent_a = VecRecall(base_dir="~/.vr", wing="team", owner="agent-a")
agent_b = VecRecall(base_dir="~/.vr", wing="team", owner="agent-b")

agent_a.add("A's private note", owner="agent-a")                # private: A only
mem = agent_a.add("Team rule: code review needs two approvals",
                  visibility="shared", memory_type="procedural")  # shared: whole wing

agent_b.search("private note")   # -> A's private memory is not visible

# Atomic changes (for external orchestration)
agent_a.update_visibility(mem.id, "shared")
agent_a.update_retention_policy(mem.id, "immutable")
```

> Authorization (who may write shared memories) is up to the caller or the interface layer; the core only filters by owner / visibility. Legacy rows with an empty owner are treated as public, and an instance without an owner applies no filter, so single-machine behavior matches v2.1.

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

v2.0's embedding has two tiers. Since v2.2 the public benchmark [LongMemEval](https://github.com/xiaowu0162/LongMemEval) (ICLR 2025) is the official recall evaluation; the small built-in set is only a quick smoke test:

| Embedding backend | LongMemEval-S R_any@5 | LongMemEval-S R_any@10 | Built-in R@5 | Dependency |
|--|--|--|--|--|
| Lexical (`bow`, default, zero-dependency) | 75.3% | 84.9% | 76.2% | None |
| Semantic (`sentence`, multilingual MiniLM) | TBD | TBD | 100% | `pip install -e ".[sentence]"` |

LongMemEval-S: 470 questions (the 30 abstention questions are skipped, as in the official setup), about 48 sessions per question, one memory per session, pure vector retrieval; retrieval only, no QA scoring. Per-type results and instructions are in [benchmarks/README.md](benchmarks/README.md); raw results are in `benchmarks/results/`.

- **Zero-dependency lexical enhancement**: `BagOfWordsEmbeddingBackend` was upgraded from "whole word + Chinese bigrams" to "whole word + English character 3/4-grams (subword) + Chinese trigrams", capturing word-form variants (running/run) and spelling variants (database/databases) with no external dependency.
- **Multilingual semantic model**: the `sentence` / `auto` default model was upgraded from English `all-MiniLM-L6-v2` to multilingual `paraphrase-multilingual-MiniLM-L12-v2` (Chinese-friendly), overridable via `--embed-model`.
- The eval set deliberately includes semantically-equivalent paraphrases with zero lexical overlap (e.g. "程序崩溃了怎么办" → "应用 crash 时的排查步骤"), but with only 36 candidates it is far too small: 100% there says little about real-world recall. Use the LongMemEval numbers. The 96.6% claimed in v1 was a reproduction of the "original's pure-vector ceiling" (old eval set, no script); from v2 onward it is re-quantified with a reproducible benchmark.

```bash
python benchmarks/benchmark_recall.py          # Built-in smoke test (lexical)
python benchmarks/benchmark_recall.py --all    # Built-in smoke test (lexical + semantic)
python benchmarks/longmemeval.py --data benchmarks/data/longmemeval_s_cleaned.json   # LongMemEval (download data first)
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

### Write Governance, Retrieval Decisions, and Repository Map

v2.1 adds four independently opt-in mechanisms; default behavior is unchanged:

- **Pre-write redaction**: `redact_memory_payload()` uses a prioritized rule table for 8 sensitive classes (private keys, tokens, cookies, API keys, emails, etc.), with a whitelist so `${VAR}` / `example` are not false positives. `VecRecall(redact_payloads=True)` redacts before persistence and records counts in `metadata.redaction_counts`. Redaction is irreversible and off by default; it complements at-rest encryption (encryption does not prevent plaintext from being written).
- **Writeback governance**: `WritebackGovernor` supplies idempotency keys, a buffer triggered by turn / char / message / idle limits, and retries. `SqliteOutbox` persists pending writebacks in the `writeback_outbox` table of `knowledge.db`; use `vr writeback stats|recover` to inspect or recover.
- **Retrieval decisions**: `decide_recall(query, context)` decides whether retrieval is worthwhile, `build_query_intent()` normalizes the query, and `evaluate_memory()` applies three acceptance checks (same boundary / same behavior / actionable). MCP exposes `mp_recall_policy` and `mp_accept_memory`; `mp_search`'s `acceptance_query` attaches verdicts **without changing recall or ranking**.
- **Repository map**: `vr repo-map build|update|validate` writes structure, entry points, module responsibilities and git evidence into `.vecrecall/repo/`, updates incrementally from a baseline SHA, and validates the bundle. The output is **never indexed as vectors** — it stays explicit context, preserving retrieval/organization decoupling.

```python
from vecrecall import (
    VecRecall, redact_memory_payload, decide_recall,
    evaluate_memory, build_repo_map, validate_repo_map,
)

print(decide_recall("Why did the earlier API migration fail?").should_recall)  # True
palace = VecRecall("~/.vr", redact_payloads=True)
palace.add("token = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789'")

report = build_repo_map(".")
print(report["git_status"], validate_repo_map(".")["ok"])
```

```bash
vr recall-policy "Why did the earlier API migration fail?"
vr writeback stats
vr repo-map build --repo .
vr repo-map update --repo .
vr repo-map validate --repo .
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
| **Tamper-proof (integrity)** | `--chain` hash chain: each write generates a block, chained per `(wing, topic_key)`; `mp_verify` / `vr verify` detect content tampering |
| **Key never stored on disk** | The remote embedding API key is read only from environment variables, never written to config.json |

**Note**: encryption is off by default, so memory content is written to `knowledge.db` in plaintext. For sensitive content, enable at-rest encryption (`vr init --encrypt` or `encryption_key`, see above) or pre-write redaction (`redact_payloads=True`). `python scripts/audit_secrets.py` scans stored memories read-only for leaked credentials.

---

## Four-Layer Memory Stack

Each AI wake-up loads only 600–900 tokens, instead of stuffing all history into the prompt.

```
L0  Identity layer       ~50 tokens    Loaded every time, fixed
L1  Key moments          ~600 tokens   Top-15 by importance, no wing filter; immutable memories always included
L2  Semantic context     ~300 tokens   Loaded when similarity to current dialogue ≥ 0.55
L3  Deep retrieval       On demand     Full semantic search, hits vector store directly
```

The L2 change is critical: the original used Room name matching to trigger loading. VecRecall uses a semantic similarity threshold instead.
The threshold is adjustable: `vr-mcp` tool `mp_set_l2_threshold`, or in code `palace.L2_TRIGGER_THRESHOLD = 0.6`.

---

## MCP Tools (34 total)

**Retrieval decisions**
- `mp_recall_policy` — decide whether the current task warrants retrieval, with a normalized query intent
- `mp_accept_memory` — three acceptance checks (same boundary / same behavior / actionable); `mp_search`'s `acceptance_query` attaches verdicts without changing ranking

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
- `mp_link` — Create cross-wing association (since v2.2, only between memories that are both shared)

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

## Extension Hooks

v2.2 adds `vecrecall.core.hooks` so extension packages can attach behavior without modifying the core. No hooks are registered by default, so behavior is unchanged.

| Hook point | When | Signature | Typical use |
|---|---|---|---|
| `before_write` | Before a write | `fn(fields) -> fields \| None`; raise to reject the write | Write authorization, tenant tagging, field rewriting |
| `after_write` | After a write (including dedup merges) | `fn(memory, merged)` | Audit logging, syncing to other systems |
| `recall_filter` | Before results are returned | `fn(memories, viewer) -> memories`; may only remove items | Read authorization, tenant isolation |
| `on_delete` | After a delete | `fn(memory_id)` | Auditing, cascading cleanup |

`recall_filter` applies to `search`, `build_context` (L1/L2/L3), `query_procedural`, `query_by_topic_key` and `graph_search`.

```python
from vecrecall import VecRecall

palace = VecRecall(base_dir="~/.vr", wing="team", owner="agent-a")

# 1. Register callbacks directly
def deny_secrets(fields):
    if "INTERNAL ONLY" in fields["content"]:
        raise PermissionError("write not allowed")
palace.hooks.register("before_write", deny_secrets)
palace.hooks.register("after_write", lambda mem, merged: print("wrote", mem.id, merged))

# 2. Plugin object: implement register(registry)
class TenantPlugin:
    def register(self, registry):
        registry.register("recall_filter",
                          lambda mems, viewer: [m for m in mems
                                                if m.metadata.get("tenant") == "t1"])
palace.hooks.install(TenantPlugin())
```

Plugins can also be discovered through the `vecrecall.plugins` entry point group, enabled explicitly with `VecRecall(load_plugins=True)`:

```toml
# pyproject.toml of the extension package
[project.entry-points."vecrecall.plugins"]
my_plugin = "my_package.plugin:TenantPlugin"
```

The core only defines the hook points; it contains no authorization, compliance or business logic.

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
python tests/test_blockchain.py
python tests/test_governance.py
python tests/test_recall_policy.py
python tests/test_repo_map.py
```

`test_core.py` includes a core-boundary guard: core sources must not contain scenario schemas (such as `contract_id`) or import `vecrecall.adapters`.

---

## v2.2 Upgrade Notes (Runtime Primitives)

v2.2 brings typed, isolated, immutable and per-key-chained memory into the public core. Every new parameter has a default, existing code needs no changes, and old databases migrate automatically on open.

| Change | Details |
|---|---|
| **Typed memory** | New `memory_type` (episodic / semantic / procedural), orthogonal to L0–L3 loading. `semantic` always enters the temporal graph (heuristic fallback without an extractor); `procedural` is matched exactly with `query_procedural(scenario_tags, version)` |
| **Type filtering** | `build_context(memory_types=[...])` filters in one place for L1/L2/L3 |
| **Isolation** | New `visibility` (private/shared) and `owner`, forming a triple with wing; atomic `update_visibility()` |
| **Immutable memory** | New `retention_policy` (decay default / immutable). Immutable memories skip dedup, forgetting, pruning, distillation and redaction, and are always in L1; atomic `update_retention_policy()` |
| **Per-key hash chains** | `blocks` gains a `topic_key` column; chains are built and verified per `(wing, topic_key)`; the default chain (`topic_key=""`) stays backward compatible |
| **Topic queries** | `query_by_topic_key(prefix)` + `verify_integrity(topic_key=...)` verify a single topic chain |
| **Tighter cross-wing links** | `mp_link` across wings only for memories that are shared on both sides |
| **Core boundary** | The core accepts only generic primitives (`memory_type` / `visibility` / `owner` / `retention_policy` / `topic_key`); scenario schemas stay out, enforced by a source-assertion test |
| **Code split** | `core/engine.py` is split into `models.py` / `vectors.py` / `embeddings.py` / `storage.py`; `from vecrecall.core.engine import ...` keeps working |
| **Extension hooks** | New `core/hooks.py`: `before_write` / `after_write` / `recall_filter` / `on_delete`; see "Extension Hooks" above |
| **Unified credential rules** | `guard.py` delegates to `redaction.py`, merging the two rule sets; adds detection of inline assignments in prose |
| **Fix: search dropped negative-score results** | `search()` and L3 now default to `min_score=None` and always return top-n. **Behavior change**: pass `min_score=0.0` explicitly to keep the old cutoff |
| **Public benchmark** | New `benchmarks/longmemeval.py`; LongMemEval-S lexical baseline R_any@5 75.3% |

> Known limit: immutable memories only grow; long-running deployments need cold-storage archiving (not implemented).

---

## v2.1 Upgrade Notes (Governance Layer)

v2.1 leaves the v2.0 cognitive layer and retrieval semantics untouched and adds governance only: pre-write redaction (`redact_payloads`, opt-in and irreversible), writeback governance (idempotency / buffering / retries + `writeback_outbox`), retrieval decisions (three checks attached without changing ranking), and repository maps (`vr repo-map`, never vector-indexed). New CLI subcommands: `writeback` / `recall-policy` / `repo-map`; MCP tools grow from 32 to 34. The version is uniformly `2.1.0` in `pyproject.toml`, `vecrecall/__init__.py`, `api/server.py`, and `mcp/server.py`.

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
