# memorable-ai

Measured memory layer for coding agents. Store, distill, and retrieve past session context instead of re-sending full history to the LLM.

**Proven results on real data:** 99.7% token savings, 11ms retrieval latency, with an eval harness that measures every claim.

## Install

```bash
npm install -g memorable-ai
# or
bun install -g memorable-ai
# or use directly
npx memorable-ai --help
```

Requires Python 3.11+ (auto-detected during install).

## Quick start

```bash
# 1. Start the daemon — auto-ingests Claude Code transcripts as sessions end
memorable daemon

# 2. Ingest an existing project's transcripts
memorable ingest-project ~/.claude/projects/-Users-you-your-project --project myproject

# 3. Query past context
memorable query "how does auth work" --project myproject

# 4. Distill sessions into compact, reusable memories
memorable distill --project myproject

# 5. Launch the inspector UI
memorable inspector
```

## How it works

```
Session ends → auto-ingest (noise-filtered) → distill (LLM extracts decisions/lessons/patterns)
                                                  ↓
New session → skill/query retrieves scoped context → 100-1000 tokens instead of 500K+
```

**Storage:** SQLite + sqlite-vec (zero infrastructure, single file, <15ms retrieval)

**Embeddings:** Local sentence-transformers (offline, free) or any OpenAI-compatible API

**Distillation:** LLM extracts typed memories (decision/lesson/snippet/bugfix) with dedup and contradiction handling (supersede)

**Eval:** Built-in harness with 4 baselines (no-memory, last-N, naive-RAG, memory) + ablation tests. Nothing ships without a measured delta.

## Commands

| Command | Description |
|---|---|
| `memorable daemon` | Auto-ingest daemon — watches ~/.claude/projects/ for new transcripts |
| `memorable ingest-project <dir> --project <name>` | Bulk ingest a project's transcripts |
| `memorable ingest-cc <file> --project <name>` | Ingest a single transcript |
| `memorable ingest-doc <file> --project <name>` | Ingest a markdown/notes file |
| `memorable distill --project <name>` | Distill sessions into memories (requires ANTHROPIC_API_KEY) |
| `memorable query <text> --project <name>` | Query for relevant context |
| `memorable eval <cases.json>` | Run eval suite against baselines |
| `memorable build-cases --project <name>` | Auto-generate eval cases from a corpus |
| `memorable inspector` | Launch the Streamlit inspector UI |
| `memorable setup` | Re-run Python environment setup |

## Claude Code skill

The `skill/` directory contains a Claude Code recall skill. Point your agent at `skill/SKILL.md` to enable mid-session memory recall.

## Architecture

- `memorable/store/` — SQLite + sqlite-vec store (artifacts, edges, vector search)
- `memorable/embed/` — Pluggable embedders (local sentence-transformers, API, fake for tests)
- `memorable/llm/` — Pluggable LLM interface (Anthropic, OpenAI-compatible)
- `memorable/ingest/` — Transcript + document parsers with noise filtering
- `memorable/retrieve/` — Scope-filtered vector search + edge expansion + recency blending
- `memorable/distill/` — LLM distillation with dedup + supersede
- `memorable/eval/` — Eval harness, metrics, baselines, dataset builder
- `memorable/daemon.py` — Auto-ingest background watcher
- `inspector.py` — Streamlit inspector UI

## License

MIT
