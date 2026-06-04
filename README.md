```
                                                _
 _ __ ___   ___ _ __ ___   ___  _ __       __ _(_)
| '_ ` _ \ / _ \ '_ ` _ \ / _ \| '__|____ / _` | |
| | | | | |  __/ | | | | | (_) | | |_____| (_| | |
|_| |_| |_|\___|_| |_| |_|\___/|_|        \__,_|_|

  Measured memory for coding agents.
```

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-105%20passing-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)]()

**Automatic background memory for Claude Code.** Fire and forget — no API keys needed.

Memor watches your coding sessions, extracts decisions and patterns, and recalls relevant context on every prompt. Zero configuration. One install. Your agent remembers everything.

---

## Quick Start

```bash
# Install
git clone https://github.com/bnimit/memor-ai.git
cd memor-ai
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Install the Claude Code hook + start the daemon
memor install-hook
memor daemon
```

That's it. Every Claude Code prompt now gets automatic context recall. Open the dashboard to see it working:

```bash
memor dashboard
# Opens http://localhost:8420
```

---

## How It Works

```
  You type a prompt in Claude Code
      |
      v
  Hook fires (UserPromptSubmit)
      |
      v
  Embed query locally (model2vec, ~2ms)
      |
      v
  Search memory bank (sqlite-vec, ~8ms)
      |
      v
  Inject relevant context into prompt
      |
      v
  Claude sees your past decisions, bugfixes,
  architecture choices — without you re-explaining
```

**Two background processes:**

1. **Daemon** — polls `~/.claude/projects/` for transcripts, embeds chunks, runs extractive distillation. All local.
2. **Hook** — fires on every prompt, recalls relevant memories, injects them as additional context. Sub-15ms.

**No API keys required.** Embeddings run locally via [model2vec](https://github.com/MinishLab/model2vec) (potion-base-8M, 256-dim). Vectors stored in [sqlite-vec](https://github.com/asg017/sqlite-vec). Optional: set `ANTHROPIC_API_KEY` for richer abstractive distillation.

---

## What Gets Stored

| Kind | Source | Description |
|---|---|---|
| `session_chunk` | Daemon auto-ingest | Filtered turns from Claude Code transcripts |
| `memory` | Extractive distillation | Key decisions, patterns, bugfixes per session |

The daemon runs a signal filter that keeps decisions, bugfixes, lessons, and code rationale while skipping noise (tool calls, file listings, boilerplate).

---

## Dashboard

```bash
memor dashboard
```

Shows:
- **Memory bank** — session chunks, distilled memories, projects tracked
- **Recall stats** — hit rate, latency, tokens injected per query
- **Per-project breakdown** — which projects have the most context

---

## Architecture

```
memor/
+-- types.py              Core dataclasses: Artifact, Scope, Hit, RetrievalTrace
+-- interfaces.py         Protocols: Embedder, LLM, MemoryStore
+-- cli.py                Typer CLI entry point
+-- daemon.py             Auto-ingest + auto-distill background watcher
+-- project.py            Git-root project resolver
+-- recall.py             Shared recall core (used by hook + skill)
+-- hook_server.py        Unix socket sidecar (keeps embedder warm)
|
+-- store/
|   +-- sqlite_store.py   SQLite + sqlite-vec (WAL mode, dimension safety)
|
+-- embed/
|   +-- local.py          model2vec (potion-base-8M, 256-dim, ~60MB)
|   +-- api.py            OpenAI-compatible embedding API (optional)
|   +-- fake.py           Deterministic SHA-256 embedder (tests)
|
+-- dashboard/
|   +-- server.py         FastAPI dashboard backend
|   +-- static/index.html Self-contained dashboard (no CDN deps)
|
+-- distill/
|   +-- extractive.py     TF-IDF + clustering (free, local)
|   +-- distiller.py      Extractive + optional LLM abstractive
|
+-- eval/
    +-- runner.py          4-baseline eval runner
    +-- judge.py           LLM-as-judge evaluation
    +-- embed_benchmark.py Embedding model comparison

bin/memor-hook.py          Claude Code hook (thin client)
skill/recall.py            Standalone recall script
```

---

## Commands

| Command | Description |
|---|---|
| `memor daemon` | Auto-ingest daemon. Watches transcripts, embeds, distills |
| `memor install-hook` | Install the Claude Code recall hook |
| `memor dashboard` | Launch web dashboard on localhost:8420 |
| `memor query <text>` | Query for relevant context |
| `memor ingest-cc <file>` | Ingest a single transcript |
| `memor ingest-project <dir>` | Bulk ingest a project's transcripts |
| `memor ingest-doc <file>` | Ingest a markdown document |
| `memor distill --project <name>` | Run distillation manually |
| `memor eval <cases.json>` | Run eval suite |
| `memor bench-embed --project <name>` | Compare embedding models |

---

## Configuration

| Variable | Purpose | Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | Abstractive distillation (richer memories) | Optional |

Without any API key, everything works — embeddings are local, distillation uses the extractive (free) path. The API key upgrades distillation quality but isn't needed.

---

## Development

```bash
git clone https://github.com/bnimit/memor-ai.git
cd memor-ai
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,anthropic]"

pytest  # 105 tests
```

---

## License

MIT. See [LICENSE](LICENSE) for the full text.
