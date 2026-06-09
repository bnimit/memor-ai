```
                                                _
 _ __ ___   ___ _ __ ___   ___  _ __       __ _(_)
| '_ ` _ \ / _ \ '_ ` _ \ / _ \| '__|____ / _` | |
| | | | | |  __/ | | | | | (_) | | |_____| (_| | |
|_| |_| |_|\___|_| |_| |_|\___/|_|        \__,_|_|

  Measured memory for coding agents.
```

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-251%20passing-brightgreen.svg)]()
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

# Start as a background service (macOS/Linux)
memor service install

# Or run in the foreground
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
  Hybrid retrieval: dense vectors + lexical BM25, fused (RRF)
      |
      v
  Relevance gate drops off-topic matches (inject nothing if nothing fits)
      |
      v
  Rank: similarity + recency + kind weight + quality
      |
      v
  Inject relevant context into prompt
      |
      v
  Claude sees your past decisions, bugfixes,
  architecture choices — without you re-explaining
```

**Two background processes:**

1. **Daemon** — polls `~/.claude/projects/` for transcripts, embeds chunks, runs distillation, analyzes feedback (positive and negative), promotes cross-project patterns to global scope, compacts duplicates, tracks session-level token usage. All local.
2. **Hook** — fires on every prompt, recalls relevant memories, injects them as context. Sub-15ms.

**No API keys required.** Embeddings run locally via [model2vec](https://github.com/MinishLab/model2vec) (potion-base-8M, 256-dim). Vectors stored in [sqlite-vec](https://github.com/asg017/sqlite-vec). Everything runs on your machine.

---

## Hybrid Retrieval

Memor retrieves over two channels and fuses them, so it catches both semantic matches and exact terms:

- **Dense** — local vector similarity (model2vec) for semantic recall.
- **Lexical** — SQLite FTS5 / BM25 over the raw text, to recover exact identifiers, error strings, and API names that static embeddings blur together.

The two rankings are combined with **Reciprocal Rank Fusion (RRF)**. A **relevance gate** drops anti-correlated (off-topic) candidates *before* ranking, so an unrelated prompt injects nothing rather than the least-bad guess. The lexical channel only activates when the dense channel finds the query on-topic, preventing generic words from pulling in noise.

> Tunable via `MEMOR_MIN_SIMILARITY` (relevance floor, default 0.0) and `MEMOR_MAX_TOKENS` (injection budget, default 1500).

## Scoring

Surviving candidates are ranked by four signals:

| Signal | Weight | How it works |
|---|---|---|
| **Semantic similarity** | 50% | Dense + lexical relevance, fused via RRF |
| **Recency** | 25% | Exponential decay with 14-day half-life — recent decisions rank higher |
| **Kind weight** | 15% | Distilled memories (1.3x) rank above raw session chunks (1.0x) |
| **Quality** | 10% | Bayesian score from implicit feedback — memories the agent actually uses rank higher |

This means a relevant decision from yesterday beats a vaguely-related chunk from a month ago — even if the raw embedding similarity is similar.

### Feedback Loop

Memor tracks whether recalled memories actually get used by the agent — and whether they actively hurt. After each session, the daemon analyzes the transcript in both directions:

- **Positive signal** — n-gram overlap or semantic similarity between recalled content and the agent's response. Memories that consistently prove useful get quality boosts.
- **Negative signal** — user rejection ("no that's wrong", "we switched to X") or assistant contradiction ("however, looking at the current code, we actually use Y"). Memories that get corrected receive a quality penalty, making them less likely to be recalled next time.

The quality formula is Bayesian: `(uses - negatives + 1) / (recalls + 2)`. One correction weighs as much as one positive use, so harmful memories drop fast. Memories never recalled in 30+ days get automatically deactivated. Near-duplicate memories are compacted into one.

---

## What Gets Stored

| Kind | Source | Description |
|---|---|---|
| `session_chunk` | Daemon auto-ingest | Filtered turns from Claude Code transcripts |
| `memory` | Extractive distillation | Key decisions, patterns, bugfixes per session |

Memories are automatically classified as `decision`, `bugfix`, `lesson`, `snippet`, or generic `extract` based on content patterns. The daemon runs a signal filter that keeps decisions, bugfixes, lessons, and code rationale while skipping noise (tool calls, file listings, boilerplate).

---

## Global Memories

Some patterns aren't project-specific — they're yours. "Always use type hints." "Structure FastAPI apps with a `routes/` directory." "Prefer composition over inheritance."

Memor detects these automatically. When the same pattern appears in **3 or more projects** (measured by embedding similarity), the daemon promotes it to a `_global` scope:

- **Global memories are recalled everywhere** — they show up in every project's search results alongside project-specific memories.
- **Source duplicates are deactivated** — the per-project copies get superseded by the single global version, reducing clutter.
- **No manual tagging** — promotion is fully automatic, based on cross-project clustering.

This means your coding habits and preferences follow you into new projects from the first prompt, without you having to re-explain anything.

---

## Dashboard

```bash
memor dashboard
```

Dark fintech-inspired UI showing:
- **Hero metrics** — total memories, recall count, avg latency, coverage — with sparkline bars
- **Daily recall activity** — stacked bar chart of hits vs misses over time
- **Session efficiency** — real token savings measured from API usage data (avg tokens/turn with vs without recall)
- **Per-project breakdown** — artifact counts, token totals, last activity
- **Recent recalls** — every hook event with scores, latency, and status

---

## Commands

```
memor help                           Print the full manual
memor install-hook                   Install Claude Code hook + download model
memor daemon                         Auto-ingest + distill (background watcher)
memor dashboard                      Web dashboard on localhost:8420
memor version                        Print installed version
memor service install                Run daemon as background service (launchd/systemd)
memor service stop                   Stop the background service
memor service uninstall              Remove the background service
memor service status                 Check if the service is running
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
memor eval-counterfactual --project  Win/tie/loss vs no-memory baseline
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
+-- feedback.py           Feedback analyzer (positive usage + negative signals)
+-- global_memories.py    Cross-project promotion to _global scope
|
+-- retrieve/
|   +-- retriever.py      Hybrid retrieval (dense + BM25, RRF) + relevance gate + scoring
|
+-- store/
|   +-- sqlite_store.py   SQLite + sqlite-vec + FTS5 (WAL mode, dimension safety)
|
+-- embed/
|   +-- local.py          model2vec (potion-base-8M, 256-dim, ~60MB)
|   +-- api.py            OpenAI-compatible embedding API (optional)
|   +-- fake.py           Deterministic SHA-256 embedder (tests)
|
+-- service.py            Background service management (launchd/systemd)
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
- **Embeddings run locally** via model2vec static token embeddings — no inference runtime, no GPU (one-time model download from HuggingFace — no user data sent).
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

pytest  # 251 tests
```

---

## License

MIT. See [LICENSE](LICENSE) for the full text.
