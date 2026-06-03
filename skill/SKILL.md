---
name: memor-recall
description: Recall relevant past coding context (decisions, patterns, session history) for a project instead of re-deriving it from scratch. Use when starting work that resembles past sessions, debugging something that was fixed before, or when the user asks "what did we decide / how did we fix X".
---

# Memor Recall

Retrieves distilled memories and session context from the Memor memory layer, scoped to a specific project.

## When to use

- Starting a task that likely overlaps with past work
- The user asks about a previous decision, pattern, or fix
- You need architectural context for a project you have worked on before
- Before re-deriving something that was already figured out

## How to invoke

```bash
python skill/recall.py --query "<task or question>" --project "<project>"
```

### Parameters

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--query` | yes | -- | The question or task description to recall context for |
| `--project` | yes | -- | Project name to scope retrieval (e.g. `plirin`, `stablex`) |
| `--k` | no | 8 | Number of context hits to return |
| `--db` | no | `~/.memor/memor.db` | Path to the memory database |

## Output

Returns a formatted context block with numbered hits (decisions, session chunks, notes) and a trace summary showing hit count, latency, and token savings. Read the context, cite artifact IDs when using a fact, and prefer recalled decisions over re-deriving them.

## Setup

The memory database is populated by the auto-ingest daemon (`memor daemon`), which watches `~/.claude/projects/` for new session transcripts and ingests them automatically. If the database does not exist yet, run `memor daemon` in the background first.
