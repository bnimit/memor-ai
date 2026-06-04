# Phase 2: Instrumented Pipeline — Design Spec

**Goal:** Make Memor's value visible and automatic. Battle-test the ingest/distill/recall pipeline on real data, wire up seamless recall via Claude Code hooks, and build a web dashboard that shows production metrics.

**Architecture:** Three independent pieces that compose into the full "install and forget" story:
1. Hardened ingestion pipeline (noise filter + real-data validation)
2. Claude Code `UserPromptSubmit` hook for automatic recall injection
3. FastAPI web dashboard for metrics and token-savings visibility

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, Tailwind CSS (CDN), vanilla JS, SQLite

---

## 1. Battle-Test the Pipeline

### Problem

The daemon and ingestion pipeline have never processed real transcript data. The user has 810 `.jsonl` transcripts (138K lines) across 23 projects in `~/.claude/projects/`, but the memory database is empty. The current noise filter (`_is_noise` in `memor/ingest/claude_code.py`) catches filler phrases and short messages but misses several categories of real-world noise.

### Noise Filter Gaps

Extend `_is_noise` and `parse_transcript` to handle:

| Category | Detection | Action |
|---|---|---|
| Tool call results | Messages with `type` containing tool result content (Read output, Bash output, file listings) | Skip — these are ephemeral context, not durable knowledge |
| System-reminder blocks | Text containing `<system-reminder>` tags | Strip the tags and their content before processing; skip if nothing remains |
| Base64 / binary blobs | Text matching `^[A-Za-z0-9+/=]{200,}$` or containing `data:image/` | Skip entirely |
| Skill boilerplate | Already handled (`_SKILL_BOILERPLATE`) — verify coverage on real data | Keep existing filter |
| Permission prompts | Short messages about tool approval ("Allow Read...", "Permission granted") | Skip — no durable value |
| Repeated tool invocations | Duplicate or near-duplicate text within same session | Deduplicate by text hash within session |

### Validation Process

1. Run `memor daemon` on all 810 transcripts
2. Inspect ingested artifacts in SQLite: check kind distribution, token counts, sample random chunks
3. Run extractive distillation (no LLM key needed)
4. Inspect distilled memories: are they useful or just noise?
5. Iterate on filter rules until ingested content is consistently high-signal

### Files Modified

- `memor/ingest/claude_code.py` — extend `_is_noise`, update `parse_transcript` to handle tool results and system-reminder stripping

---

## 2. Claude Code Hook Integration

### Overview

A `UserPromptSubmit` hook that runs before every Claude Code prompt, recalls relevant memories, and injects them as `additionalContext`. The hook also logs every recall event for the dashboard.

### Hook Script: `bin/memor-hook.py`

**Input** (stdin JSON from Claude Code):
```json
{
  "session_id": "abc123",
  "transcript_path": "/path/to/session.jsonl",
  "cwd": "/Users/nimit/Documents/Projects/foo",
  "prompt": "Fix the auth middleware bug"
}
```

**Processing:**
1. Parse stdin JSON
2. Derive project name from `cwd` (last path component, matching daemon convention)
3. Initialize embedder (API-first, same logic as `_auto_embedder()`)
4. Check if DB exists and has artifacts for this project
5. Embed the prompt, search via `Retriever` with `k=8`
6. Apply similarity threshold: skip injection if top score < 0.3
7. Format hits into a context block
8. Append a status line (see Status Messages below)
9. Log the recall event to `recall_log` table
10. Output JSON to stdout

**Output** (stdout JSON):
```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "## Recalled Memories (project: foo)\n\n### 1. [decision] Use API embeddings...\n...\n\n---\nMemor: recalled 5 memories (312 tokens, 0.74 top score)"
  }
}
```

**Error handling:** Any exception → log to stderr, exit with code 1 (non-blocking). Claude Code continues without context. Never block the user.

**Performance target:** < 500ms end-to-end. Embedding API call is the bottleneck (~200-300ms). SQLite search is ~1ms.

### Status Messages

The hook always appends a status line to `additionalContext`:

| Scenario | Status | Message |
|---|---|---|
| Hits found, LLM distillation | `ok` | `Memor: recalled N memories (M tokens, S top score)` |
| Hits found, extractive only | `extractive_only` | `Memor: recalled N memories (extractive only — set ANTHROPIC_API_KEY for richer distillation)` |
| No relevant hits | `no_hits` | `Memor: no relevant memories for project "X" yet` |
| DB empty | `empty_db` | `Memor: memory store is empty — run "memor daemon" to start ingesting sessions` |
| No embedder | `no_embedder` | `Memor: inactive — set OPENAI_API_KEY or pip install memor-ai[local] for memory recall` |
| Error | `error` | (logged to stderr only, hook exits non-blocking) |

To detect extractive-only: query `artifacts` table for the project — if memories exist with `kind='memory'` but all have `meta` containing `"mem_type": "extract"` (from `ExtractiveDistiller`) and none have `mem_type` in `("decision", "lesson", "snippet", "bugfix")` (from LLM `Distiller`), distillation is extractive-only.

### Recall Log Schema

```sql
CREATE TABLE IF NOT EXISTS recall_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp REAL,
  project TEXT,
  query_preview TEXT,
  hits_count INTEGER,
  top_score REAL,
  tokens_injected INTEGER,
  latency_ms REAL,
  status TEXT,
  session_id TEXT
);
```

### Install Command: `memor install-hook`

A CLI command that auto-configures the hook in `~/.claude/settings.json`:

1. Read existing settings (or create empty `{}`)
2. Add/update the `hooks.UserPromptSubmit` entry pointing to `bin/memor-hook.py`
3. Set timeout to 5000ms
4. Write back
5. Print confirmation with the hook path

Must be idempotent — running twice doesn't duplicate the hook entry.

### Files Created/Modified

- Create: `bin/memor-hook.py` — standalone hook script
- Modify: `memor/cli.py` — add `install-hook` command
- Modify: `memor/store/sqlite_store.py` — add `recall_log` table to schema, add `log_recall()` and `get_recall_stats()` methods

---

## 3. Web Dashboard

### Overview

A local web dashboard served by FastAPI that visualizes Memor's production metrics. Replaces the TUI inspector as the primary UI.

### Architecture

- `memor/dashboard/server.py` — FastAPI app with JSON API endpoints
- `memor/dashboard/static/index.html` — single HTML file, Tailwind CSS (CDN), vanilla JS
- CLI command `memor dashboard` starts uvicorn on `localhost:8420`, opens browser

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/summary` | Total recalls, tokens injected, avg latency, hit rate, active project count |
| `GET` | `/api/projects` | Per-project: recall count, tokens injected, avg score, status breakdown |
| `GET` | `/api/recalls?limit=50&project=X` | Recent recall events, filterable by project |
| `GET` | `/api/savings` | Per-project: tokens recalled vs full project context size, % reduction |
| `GET` | `/api/health` | Daemon status, DB file size, artifact counts by kind, last ingest timestamp |

All endpoints read from `recall_log` and `artifacts` tables in `~/.memor/memor.db`.

### Dashboard Layout

Single-page app with 4 sections, top to bottom:

**1. Hero Metrics (top row of cards)**
- Total recalls (count)
- Tokens injected (sum)
- Hit rate (% of recalls with hits > 0)
- Avg latency (ms)
- Projects tracked (count)

Big numbers in colored cards. First thing the user sees.

**2. Token Savings (headline section)**
Per-project horizontal bars showing:
- Tokens recalled (what Memor injected)
- Full context size (total tokens for that project's artifacts)
- % reduction

Example: "project foo: 312 tokens recalled vs 48,000 full context = 99.3% reduction"

This is the selling number — the reason to keep Memor installed.

**3. Project Breakdown (sortable table)**
Columns: project name, recall count, total tokens injected, avg top score, ok/no_hits/extractive_only counts. Sortable by clicking column headers. Click a row to filter the recalls table below.

**4. Recent Recalls (table)**
Columns: timestamp, project, query preview (first 80 chars), hits, top score, tokens, latency, status badge.
- Status badges: green (ok), yellow (extractive_only), gray (no_hits)
- Auto-refreshes every 30s via `setInterval` + `fetch()`
- Filterable by project (from clicking project table above, or a dropdown)

### Styling

- Tailwind CSS via CDN (`<script src="https://cdn.tailwindcss.com">`)
- Dark mode default, light mode toggle
- Responsive: works on any screen width
- No build step, no npm, no bundler

### Dashboard Command

```
memor dashboard [--port 8420] [--no-open] [--db ~/.memor/memor.db]
```

- Starts uvicorn serving the FastAPI app
- Opens default browser to `http://localhost:{port}` unless `--no-open`
- Reads from the specified DB path

### Files Created

- `memor/dashboard/__init__.py`
- `memor/dashboard/server.py` — FastAPI app + endpoints
- `memor/dashboard/static/index.html` — complete dashboard UI

### Files Modified

- `memor/cli.py` — add `dashboard` command, remove `inspector` command

---

## 4. Cleanup: Remove TUI

### Removed

- `memor/tui/` directory (`app.py`, `screens/__init__.py`, `screens/query.py`, `screens/browse.py`, `screens/eval_screen.py`, `screens/edges.py`, `__init__.py`)
- `tests/test_tui.py`
- `inspector` command from `memor/cli.py`
- `inspector.py` (root level)
- `textual>=0.80` from `pyproject.toml` dependencies

### Dependency Changes

| Action | Package | Reason |
|---|---|---|
| Remove | `textual>=0.80` | TUI removed |
| Add | `fastapi>=0.111` | Web dashboard backend |
| Add | `uvicorn>=0.30` | ASGI server for FastAPI |

---

## Testing Strategy

### New Tests

- `tests/test_noise_filter.py` — test extended noise filter with real-world patterns (tool outputs, system-reminders, base64)
- `tests/test_hook.py` — test hook script: mock stdin/stdout, verify JSON contract, test all status scenarios, test graceful error handling
- `tests/test_recall_log.py` — test `log_recall()` and `get_recall_stats()` store methods
- `tests/test_dashboard.py` — test FastAPI endpoints with `TestClient`, verify JSON shapes, test empty DB edge case
- `tests/test_install_hook.py` — test settings.json modification (idempotency, existing hooks preserved)

### Existing Tests

All 55 existing tests continue to pass (minus `test_tui.py` which is removed).

---

## Task Breakdown

1. Extend noise filter + battle-test on real transcripts
2. Add `recall_log` table and store methods
3. Build the hook script (`bin/memor-hook.py`)
4. Build `install-hook` CLI command
5. Build FastAPI dashboard (server + HTML)
6. Add `dashboard` CLI command
7. Remove TUI, update deps
8. Tests for all new components
