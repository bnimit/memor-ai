```
                                                _
 _ __ ___   ___ _ __ ___   ___  _ __       __ _(_)
| '_ ` _ \ / _ \ '_ ` _ \ / _ \| '__|____ / _` | |
| | | | | |  __/ | | | | | (_) | | |_____| (_| | |
|_| |_| |_|\___|_| |_| |_|\___/|_|        \__,_|_|

  Measured memory for coding agents.
```

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-153%20passing-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)]()
[![PyPI](https://img.shields.io/pypi/v/memor-cli.svg)](https://pypi.org/project/memor-cli/)

**Automatic background memory for Claude Code.** Fire and forget — no API keys needed.

Memor watches your coding sessions, extracts decisions and patterns, and recalls relevant context on every prompt. Zero configuration. One install. Your agent remembers everything.

---

## Quick Start

```bash
# Install globally (recommended)
pipx install memor-cli

# Install the Claude Code hook + download embedding model (~60MB)
memor install-hook

# Start the background daemon
memor daemon
```

That's it. Every Claude Code prompt now gets automatic context recall. Open the dashboard to see it working:

```bash
memor dashboard
# Opens http://localhost:8420
```

> **Alternative install:** `pip install memor-cli` works too — just make sure `~/.local/bin` is on your PATH so the `memor` command is available.

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
  Hybrid scoring: similarity + recency + kind weight + quality
      |
      v
  Inject relevant context into prompt
      |
      v
  Claude sees your past decisions, bugfixes,
  architecture choices — without you re-explaining
```

**Two background processes:**

1. **Daemon** — polls `~/.claude/projects/` for transcripts, embeds chunks, runs distillation, analyzes feedback, compacts duplicates. All local.
2. **Hook** — fires on every prompt, recalls relevant memories, injects them as context. Sub-15ms.

**No API keys required.** Embeddings run locally via [model2vec](https://github.com/MinishLab/model2vec) (potion-base-8M, 256-dim). Vectors stored in [sqlite-vec](https://github.com/asg017/sqlite-vec). Everything runs on your machine.

---

## Hybrid Scoring

Memor doesn't just match keywords. Each memory is scored by four signals:

| Signal | Weight | How it works |
|---|---|---|
| **Semantic similarity** | 50% | Vector cosine distance between query and memory |
| **Recency** | 25% | Exponential decay with 14-day half-life — recent decisions rank higher |
| **Kind weight** | 15% | Distilled memories (1.3x) rank above raw session chunks (1.0x) |
| **Quality** | 10% | Bayesian score from implicit feedback — memories the agent actually uses rank higher |

This means a relevant decision from yesterday beats a vaguely-related chunk from a month ago — even if the raw embedding similarity is similar.

### Feedback Loop

Memor tracks whether recalled memories actually get used by the agent. After each session, the daemon analyzes the transcript to detect if recalled content appeared in the agent's responses. Memories that consistently prove useful get quality boosts; memories never recalled in 30+ days get automatically deactivated. Near-duplicate memories are compacted into one.

---

## What Gets Stored

| Kind | Source | Description |
|---|---|---|
| `session_chunk` | Daemon auto-ingest | Filtered turns from Claude Code transcripts |
| `memory` | Extractive distillation | Key decisions, patterns, bugfixes per session |

Memories are automatically classified as `decision`, `bugfix`, `lesson`, `snippet`, or generic `extract` based on content patterns. The daemon runs a signal filter that keeps decisions, bugfixes, lessons, and code rationale while skipping noise (tool calls, file listings, boilerplate).

---

## Dashboard

```bash
memor dashboard
```

Shows:
- **Memory bank** — session chunks, distilled memories, projects tracked
- **Context efficiency** — overhead %, recall precision, quality scores per session
- **Per-project breakdown** — which projects have the most context
- **Recent recalls** — every hook event with scores, latency, and status

---

## Commands

```
memor help                           Print the full manual
memor install-hook                   Install Claude Code hook + download model
memor daemon                         Auto-ingest + distill (background watcher)
memor dashboard                      Web dashboard on localhost:8420
memor query <text>                   Search memories from the CLI
memor reingest                       Wipe DB and re-ingest everything
memor reingest --project <name>      Re-ingest only one project
memor forget-stale                   Deactivate memories unused for 30+ days
memor scan                           Audit DB for leaked secrets
memor scan --purge                   Redact secrets in place
memor setup-model                    Download/retry the embedding model
memor ingest-cc <file>               Ingest a single transcript
memor ingest-project <dir>           Bulk ingest a project directory
memor ingest-doc <file>              Ingest a markdown document
memor distill --project <name>       Run distillation manually
memor eval <cases.json>              Run eval suite
memor bench-embed --project <name>   Compare embedding models
```

---

## Architecture

```
memor/
+-- types.py              Core dataclasses: Artifact, Scope, Hit, RetrievalTrace
+-- interfaces.py         Protocols: Embedder, LLM, MemoryStore
+-- cli.py                Typer CLI entry point
+-- daemon.py             Auto-ingest + auto-distill + compaction watcher
+-- project.py            Git-root project resolver (filesystem-aware)
+-- recall.py             Shared recall core (used by hook + skill)
+-- redact.py             Secret detection and redaction at ingest
+-- feedback.py           Implicit feedback analyzer (usage detection)
|
+-- retrieve/
|   +-- retriever.py      Hybrid scoring: similarity + recency + kind + quality
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
|   +-- extractive.py     TF-IDF + clustering + auto-classification
|   +-- distiller.py      Extractive + optional LLM abstractive
|
+-- eval/
    +-- runner.py          4-baseline eval runner
    +-- judge.py           LLM-as-judge evaluation
    +-- embed_benchmark.py Embedding model comparison

memor/hook_cli.py          Claude Code hook entry point (thin client)
skill/recall.py            Standalone recall script
```

---

## Security

**Nothing leaves your machine.** In the default configuration:

- **No telemetry, no analytics, no phone-home.** Zero outbound network calls.
- **Embeddings run locally** via model2vec ONNX (one-time model download from HuggingFace — no user data sent).
- **Hook transport is a Unix socket** (`~/.memor/hook.sock`), not a network port.
- **Dashboard binds localhost only.**

The only optional network paths are the LLM-based abstractive distiller (requires explicitly setting `ANTHROPIC_API_KEY`) and the API embedding backend — both off by default.

### Secret redaction

Memor automatically redacts secrets **at ingest**, before anything is embedded or stored:

- API keys (AWS `AKIA...`, OpenAI `sk-...`, Anthropic `sk-ant-...`, GitHub `ghp_...`, Stripe, Slack)
- JWTs, PEM private key blocks
- Connection strings (`postgres://`, `mongodb://`, `redis://`, etc.)
- `.env`-style assignments (`DB_PASSWORD=...`, `API_KEY=...`)
- High-entropy tokens (Shannon entropy > 4.0, length > 20)

Redacted content is replaced with `[REDACTED]` in place, preserving surrounding context. To audit and clean an existing database: `memor scan` (audit) or `memor scan --purge` (redact in place).

### Contradiction handling

When a new memory contradicts an older one in the same project (detected via replacement cues like "switched from X to Y", "no longer", "ripped out"), the older memory is automatically deactivated. This prevents stale decisions from being recalled and misleading the agent.

### Local storage

The memory database (`~/.memor/memor.db`) is stored as plaintext SQLite on disk. For at-rest protection, we recommend enabling OS-level full-disk encryption (FileVault on macOS, LUKS on Linux) which covers all local files with zero performance overhead.

---

## Development

```bash
git clone https://github.com/bnimit/memor-ai.git
cd memor-ai
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest  # 153 tests
```

---

## License

MIT. See [LICENSE](LICENSE) for the full text.
