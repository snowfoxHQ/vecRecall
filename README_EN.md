# VecRecall

[![PyPI](https://img.shields.io/pypi/v/vecrecall)](https://pypi.org/project/vecrecall/)
[![CI](https://github.com/snowfoxHQ/vecRecall/actions/workflows/ci.yml/badge.svg)](https://github.com/snowfoxHQ/vecRecall/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10+-brightgreen)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/snowfoxHQ/vecRecall/blob/main/LICENSE)

**A local-first memory store for AI agents and apps.** It keeps the original text, recalls what is relevant, and puts only a few hundred tokens into the prompt. Zero dependencies by default, with everything stored in local SQLite; multi-agent isolation and immutable audit records are built in when you need them.

中文：[README.md](https://github.com/snowfoxHQ/vecRecall/blob/main/README.md) · Release history: [CHANGELOG.md](https://github.com/snowfoxHQ/vecRecall/blob/main/CHANGELOG.md)

| | Zero-dependency default | With the semantic model |
|---|---|---|
| LongMemEval-S recall (R_any@5, 470 questions) | **94.7%** | **98.3%** |
| Context put into the prompt per recall (top-5) | ~1,100 tokens | ~1,200 tokens |
| Extra dependency | none | `sentence-transformers` |

The numbers are reproducible; see [Recall](#recall).

---

## Install

```bash
pip install vecrecall                    # zero-dependency: lexical embedding + BM25 + SQLite
pip install "vecrecall[sentence]"        # multilingual semantic model (higher recall, one encoding per write)
pip install "vecrecall[crypto]"          # at-rest encryption
pip install "vecrecall[full]"            # all of the above + ChromaDB
```

From source: `git clone https://github.com/snowfoxHQ/vecRecall && cd vecRecall && pip install -e .`

If `sentence-transformers` is installed, `VecRecall()` uses the semantic model automatically; otherwise it falls back to the zero-dependency lexical embedding. You can also pass `embedding_backend=` explicitly; see [backends](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md#后端可插拔) (the detailed guide is in Chinese).

---

## Five-minute tour

```python
from vecrecall import VecRecall

with VecRecall(base_dir="~/.vr", wing="my-project") as palace:
    # Write: the original text is stored verbatim
    palace.add("Switched from MySQL to PostgreSQL because PostgreSQL has better JSON support",
               topic="database", importance=0.9)

    # Archive a conversation: one memory per turn, sharing one session_id
    palace.archive_session([
        {"role": "user", "content": "I just adopted a golden retriever named Max"},
        {"role": "assistant", "content": "Congratulations! Start with crate training"},
    ], session_id="chat-0525")
    palace.search("what is my dog's name", n=1)   # hits the "named Max" turn

    # Search: rrf by default (vector + BM25 fused by rank)
    for r in palace.search("why PostgreSQL instead of MySQL", n=5):
        print(r.score, r.memory.content)

    # Build the context to put into the prompt
    ctx = palace.build_context(current_query="how good is PostgreSQL JSON support", load_l3=True)
    print(ctx.total_tokens_estimate)
```

Command line:

```bash
vr init --dir ~/.vr --wing my-project

# Write two memories
vr add "Login JWT lifetime changed from 1 hour to 24 hours; refresh tokens stay at 30 days" --topic auth
vr add "Order service database connection pool limit raised to 100" --topic database

# Archive a conversation (format below); one memory per turn by default
vr archive chat.json

# Search: rrf by default; switch with --mode vector|bm25|rrf
vr search "how long is the JWT lifetime"

# Build the context to put into a prompt: L1 key memories + memories related to the question
vr context "how long should the JWT lifetime be" --l3
```

`chat.json` is a list of messages:

```json
[
  {"role": "user", "content": "The staging Redis host is now 10.0.0.12"},
  {"role": "assistant", "content": "Got it, I updated the config notes"}
]
```

With Claude Code or any other MCP client:

```json
{ "mcpServers": { "vecrecall": { "command": "vr-mcp", "args": ["--dir", "~/.vr", "--wing", "my-project"] } } }
```

---

## Design

**Retrieval is separate from organization.** `wing` (project) and `topic` are for browsing and organizing only; they never filter retrieval, which always runs over all memories. Filtering retrieval by folder silently drops relevant memories that were filed in the wrong place.

**Store the original; summaries are for people.** Written content is kept verbatim. Summaries such as `ui_summary` are shown in listings and never take part in retrieval.

**Store per turn, recall on demand.** Conversations are archived one memory per turn by default. Compared with one memory per session, the matching turn is no longer diluted by the rest of a long session, and the recalled context shrinks to about a twelfth.

**Four-layer context.** Each wake-up loads only what it needs:

```
L0  identity        ~50 tokens    always loaded
L1  key memories    ~600 tokens   top-15 by importance; immutable memories always included
L2  semantic        on demand     loaded when similarity to the current topic ≥ 0.55
L3  deep search     on request    rrf search by default
```

**rrf search by default.** Vector search and BM25 lexical search run side by side, and their results are merged by rank only (Reciprocal Rank Fusion), never by comparing raw scores. Semantic models handle paraphrases, BM25 handles exact terms, and the two complement each other. With zero dependencies this is 2.6 points above pure vector search; with the semantic model it beats BM25 alone on every metric. Use `mode="vector"` or `mode="bm25"` to switch.

---

## Recall

The benchmark is the S split of [LongMemEval](https://github.com/xiaowu0162/LongMemEval) (ICLR 2025): 500 questions, each with a history of about 48 sessions and ~490 turns, labeled with the sessions that contain the answer. As in the official setup the 30 abstention questions are skipped, leaving 470; retrieval only, no QA scoring.

| Configuration (one memory per turn) | Embedding | R_any@1 | R_any@5 | R_any@10 | MRR | Preference R_any@5 |
|--|--|--|--|--|--|--|
| `rrf` (default) | semantic `sentence` | **91.3%** | **98.3%** | **98.9%** | **0.944** | **96.7%** |
| `vector` | semantic `sentence` | 85.7% | 96.4% | 97.0% | 0.904 | 90.0% |
| `bm25` | not needed | 87.9% | 96.6% | 98.1% | 0.914 | 80.0% |
| `rrf` (default) | lexical `bow` (zero-dependency) | 87.0% | 94.7% | 98.3% | 0.907 | 66.7% |
| `vector` | lexical `bow` | 77.9% | 92.1% | 94.3% | 0.835 | 46.7% |
| `vector`, one memory per session (v2.2 default) | lexical `bow` | 50.0% | 75.3% | 84.9% | 0.611 | 26.7% |

- R_any@k: at least one answer session among the top k. Session-level recall: retrieved memories are mapped to their sessions and deduplicated.
- The semantic model is the default `paraphrase-multilingual-MiniLM-L12-v2`. Encoding takes about 14 ms per memory on an ordinary desktop CPU; that is the extra cost per write.
- The preference category has only 30 questions (about ±4 points); other categories are in the raw reports.

Reproduce:

```bash
python benchmarks/longmemeval.py --data benchmarks/data/longmemeval_s_cleaned.json \
    --granularity turn --configs vector,bm25,rrf [--embedder sentence]
```

Data download, all options and per-category results: [benchmarks/README.md](https://github.com/snowfoxHQ/vecRecall/blob/main/benchmarks/README.md). Raw results: [benchmarks/results/](https://github.com/snowfoxHQ/vecRecall/tree/main/benchmarks/results/).

---

## Scale and performance

Latency of the default setup (SQLite vector backend + lexical embedding) at different sizes, in ms, p50:

| Memories | `rrf` search (default) | `bm25` search | `vector` search | Write (no dedup) | Write (`add()` default dedup) | Disk |
|---|---|---|---|---|---|---|
| 1,000 | 44 | 0.3 | 45 | 4.3 | 47 | 6 MB |
| 10,000 | 449 | 2.2 | 454 | 4.5 | 427 | 60 MB |
| 50,000 | 2,303 | 12 | 2,267 | 4.4 | 2,152 | 300 MB |
| 100,000 | 4,907 | 30 | 4,631 | 4.5 | 4,324 | 600 MB |

- **Up to a few thousand memories** (typical personal use), every operation stays within a few hundred milliseconds.
- **The bottleneck is the default SQLite vector backend**: it computes similarity row by row in pure Python, so `vector` / `rrf` search and writes with dedup grow linearly with the number of memories, about 0.045 ms per memory. BM25 itself is fast: 30 ms p50 at 100,000 memories.
- The first `rrf` / `bm25` search builds the BM25 index (about 1.3 s at 10,000 memories, 14 s at 100,000). Long-running MCP / REST servers pay this once; every `vr` CLI call is a new process and rebuilds it.

With large stores:

- Write with `archive_session()` (per-turn mode skips dedup by default) or `add(..., dedup=False)`, so writes no longer slow down as the store grows.
- Switch to the Chroma backend (`pip install "vecrecall[chroma]"`, `vector_backend=ChromaVectorBackend(...)`), which uses an ANN index instead of a full scan.
- Use `mode="bm25"` when lexical search is enough.

ANN / vectorized scoring for the default backend is planned for v2.4. Corpus: real conversation turns from LongMemEval-S; machine: an ordinary Windows desktop CPU, Python 3.14. Full report: [benchmarks/results/scale_bow.md](https://github.com/snowfoxHQ/vecRecall/blob/main/benchmarks/results/scale_bow.md). Reproduce: `python benchmarks/bench_scale.py --data benchmarks/data/longmemeval_s_cleaned.json`.

---

## Features

The detailed guide is in Chinese: [docs/guide.md](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/guide.md).

| Feature | What it does |
|---|---|
| Cognitive layer | Extracts topic, importance, summary, entities and relations on write; heuristic (zero-dependency) or an Ollama / OpenAI-compatible LLM |
| Semantic dedup | Merges into an existing memory when similarity ≥ 0.92, incrementing its count |
| Temporal knowledge graph | Tracks entities, relations and how attributes change over time |
| Multi-hop search | Expands vector hits along entity relations and returns evidence paths |
| Forgetting curve & distillation | Memory strength decays over time; fragmented memories can be compressed into one long-term memory |
| Typed memory | episodic / semantic / procedural; procedural memories are matched exactly by scenario tags + version |
| Multi-agent isolation | `owner` × `visibility` (private / shared); private memories are visible only to their writer |
| Immutable memory & hash chains | `retention_policy="immutable"` is never deduped, forgotten or pruned; hash chains are split and verified per `topic_key` ([blockchain archive](https://github.com/snowfoxHQ/vecRecall/blob/main/README_BLOCKCHAIN.md)) |
| At-rest encryption | Encrypts content, summaries and metadata on disk |
| Write governance | Pre-write redaction, idempotent writeback queue, recall decisions, repository maps |
| Reasoning chains | Records observations / hypotheses / decisions / actions, scores them against the outcome, and writes effective decisions back to long-term memory |
| Extension hooks | `before_write` / `after_write` / `recall_filter` / `on_delete`; plugins without touching the core |
| Plugins | Trace (local event log); memory-card gate (unverified experience cannot be stored as "verified") |
| Pluggable backends | Vectors: SQLite (default) / Chroma / in-memory. Embeddings: lexical / sentence-transformers / Ollama / OpenAI-compatible API |

---

## Interfaces

| Interface | Entry point | Notes |
|---|---|---|
| Python | `from vecrecall import VecRecall` | everything |
| MCP | `vr-mcp` / `vr mcp` | 34 tools, for Claude Code, Gemini CLI and other MCP clients |
| REST | `vr-api` / `vr api` | zero-dependency HTTP JSON API, `127.0.0.1:8791` by default |
| CLI | `vr` | 29 subcommands |

Full tool, endpoint and command lists: [docs/interfaces.md](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/interfaces.md).

---

## Security and privacy

- Runs entirely locally; core features need no API key. Data lives in `~/.vecrecall` by default.
- **Not encrypted by default**: content is written to `knowledge.db` in plaintext. For sensitive content enable at-rest encryption (`vr init --encrypt`) or pre-write redaction (`redact_payloads=True`). `python scripts/audit_secrets.py` scans stored memories read-only for leaked credentials.
- At-rest encryption covers `knowledge.db` only; the vector index, knowledge graph and hash-chain databases are not encrypted yet.
- REST: with `--api-key`, a Bearer token is required; binding to a non-loopback address requires a token; no CORS headers by default.
- MCP import / export paths are confined to the data directory; all SQL is parameterized.

---

## Development

```bash
pip install -e ".[crypto]"
for t in core blockchain governance recall_policy repo_map lexical trace reasoning memory_card; do
  python tests/test_$t.py
done
python benchmarks/benchmark_recall.py    # built-in smoke benchmark, a few seconds
```

- Every push to `main` and every PR runs all tests on Linux (Python 3.10–3.12) and Windows (3.12) in GitHub Actions, plus a packaging check.
- Releases: pushing a `v*` tag builds and publishes to PyPI automatically (Trusted Publishing); see [RELEASING.md](https://github.com/snowfoxHQ/vecRecall/blob/main/RELEASING.md).
- Known issues and roadmap: [IMPROVEMENT_PLAN.md](https://github.com/snowfoxHQ/vecRecall/blob/main/IMPROVEMENT_PLAN.md).

---

## Release history

See [CHANGELOG.md](https://github.com/snowfoxHQ/vecRecall/blob/main/CHANGELOG.md); detailed upgrade notes for earlier versions are in [docs/upgrade-notes.md](https://github.com/snowfoxHQ/vecRecall/blob/main/docs/upgrade-notes.md).

## License

MIT
