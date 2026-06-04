# Phase 2: Instrumented Pipeline — Design Spec

**Goal:** Make Memor's value visible and automatic. Battle-test the ingest/distill/recall pipeline on real data, wire up seamless recall via Claude Code hooks, and build a web dashboard that shows production metrics.

**Architecture:** Three independent pieces that compose into the full "install and forget" story:
1. Hardened ingestion pipeline (noise filter + real-data validation)
2. Claude Code `UserPromptSubmit` hook for automatic recall injection
3. FastAPI web dashboard for metrics and token-savings visibility

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, inline CSS (no CDN), vanilla JS, SQLite (WAL mode)

---

## 1. Battle-Test the Pipeline

### Problem

The daemon and ingestion pipeline have never processed real transcript data. The user has 810 `.jsonl` transcripts (138K lines) across 23 projects in `~/.claude/projects/`, but the memory database is empty. The current noise filter (`_is_noise` in `memor/ingest/claude_code.py`) catches filler phrases and short messages but misses several categories of real-world noise.

### Noise Filter: Whitelist Approach

The current blacklist filter (listing things to reject) will always be a game of whack-a-mole. Instead, adopt a **whitelist approach**: define what constitutes signal, and drop everything else.

**Signal categories** (what we keep):

| Category | Detection | Examples |
|---|---|---|
| User questions/requests | `role=user`, token_count >= 20 | "Fix the auth middleware bug", "How does the retriever work?" |
| Architectural decisions | Assistant text matching decision patterns | "We decided to use X instead of Y because..." |
| Bug explanations | Assistant text matching bugfix patterns | "The fix is...", "The root cause was..." |
| Lessons/patterns | Assistant text matching lesson patterns | "Always use X when...", "Never do Y because..." |
| Code rationale | Assistant text with code blocks + explanation | Structured responses explaining code choices |
| Error diagnosis | Assistant text explaining errors | "This fails because...", "The issue is..." |

**Noise categories** (what we drop — anything not in the whitelist, plus explicit filters for):

| Category | Detection | Reason |
|---|---|---|
| Tool call results | Messages where the JSONL record `type` is a tool result (not `user`/`assistant`) | Ephemeral context, not durable knowledge |
| System-reminder blocks | Text containing `<system-reminder>` tags | Strip tags and content; skip if nothing remains |
| Base64 / binary blobs | Text matching `[A-Za-z0-9+/=]{200,}` or containing `data:image/` | Binary noise |
| Permission prompts | Short messages about tool approval | No durable value |
| Pure file listings | Messages that are only file paths/directory listings | Ephemeral |
| Filler phrases | Already handled (`_FILLER_STARTS`) + extended patterns | No durable value |
| Repeated content | Duplicate text hash within same session | Dedup |

The whitelist scoring approach: each chunk gets a signal score based on which signal categories it matches. Chunks scoring 0 (matching no signal category) are dropped. This is more maintainable than an ever-growing blacklist, and biases toward keeping high-value content.

### Validation Process

1. Run `memor daemon` on all 810 transcripts
2. Inspect ingested artifacts in SQLite: check kind distribution, token counts, sample random chunks
3. Run extractive distillation (no LLM key needed)
4. Inspect distilled memories: are they useful or just noise?
5. Iterate on filter rules until ingested content is consistently high-signal

### Files Modified

- `memor/ingest/claude_code.py` — replace `_is_noise` with whitelist-based `_signal_score`, update `parse_transcript` to handle tool results and system-reminder stripping

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
2. Derive project name from `cwd` using the canonical project resolver (see Section 2.1)
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

**Performance target:** < 500ms end-to-end. See Section 2.2 for how this is achieved.

### 2.1 Canonical Project Resolution

**Problem:** The daemon and hook derive project names differently. The daemon reads from `~/.claude/projects/-Users-nimit-Documents-Projects-foo/` and extracts `foo`. The hook reads CWD like `/Users/nimit/Documents/Projects/foo`. These must match exactly, or the hook queries the wrong project and silently returns nothing.

**Edge cases that break "last path component":**
- Monorepos: CWD is `/Users/nimit/Projects/big-repo/packages/auth` → `auth` (wrong, should be `big-repo`)
- Home directory: CWD is `~` → `nimit` (meaningless)
- Git subdir: CWD is `/Users/nimit/Projects/foo/src/lib` → `lib` (wrong)

**Solution:** A shared `resolve_project(cwd: str) -> str` function in `memor/project.py` used by both the daemon and the hook:

1. Walk up from CWD to find the git root (`git rev-parse --show-toplevel` or scan for `.git`)
2. Use the git root's directory name as the project name
3. If not in a git repo, check if CWD matches a known project in the `artifacts` table (fuzzy: check if any existing project name is a suffix of the CWD path)
4. Final fallback: last component of CWD

The daemon's `_project_name_from_dir` is updated to use the same logic: decode the dash-encoded path, find the git root equivalent, extract the name.

**Files:**
- Create: `memor/project.py` — `resolve_project(cwd)` function
- Modify: `memor/daemon.py` — `_project_name_from_dir` delegates to shared resolver
- `bin/memor-hook.py` — uses `resolve_project(cwd)`

### 2.2 Hook Performance: Warm Sidecar

**Problem:** The hook spawns a fresh Python process per prompt. Even for API embeddings, import time + httpx client init + sqlite-vec loading adds up. For local ONNX, loading the model into memory each time (2-5s) blows the 500ms budget entirely.

**Solution:** A lightweight sidecar daemon that keeps the embedder and DB connection warm.

`memor/hook_server.py` — a Unix domain socket server (`~/.memor/hook.sock`) that:
- Starts automatically on first hook invocation if not running
- Keeps the embedder and SqliteStore loaded in memory
- Accepts JSON requests (same format as hook stdin), returns JSON responses
- Auto-exits after 10 minutes of inactivity (no leaked processes)
- PID written to `~/.memor/hook.pid` for lifecycle management

`bin/memor-hook.py` becomes a thin client:
1. Try connecting to `~/.memor/hook.sock`
2. If socket exists and responds → send request, get response, print to stdout (~10ms)
3. If socket doesn't exist → start `memor/hook_server.py` in background, wait for socket, then proceed
4. If socket connect fails → fall back to inline execution (cold path, slower but works)

This means:
- First prompt after boot: ~1-2s (start sidecar + warm up)
- Subsequent prompts: ~50-100ms (socket round-trip + API embedding call)
- Local ONNX: first prompt ~3-5s (model load), subsequent ~50ms (model already in memory)

**Files:**
- Create: `memor/hook_server.py` — Unix domain socket sidecar
- Modify: `bin/memor-hook.py` — thin client that talks to sidecar, falls back to inline

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

To detect extractive-only: query `artifacts` table for the project — if memories exist with `kind='memory'` but all have `meta` containing `"mem_type": "extract"` (from `ExtractiveDistiller`) and none have `mem_type` in `("decision", "lesson", "snippet", "bugfix")` (from LLM `Distiller`), distillation is extractive-only. If no `kind='memory'` artifacts exist at all (only raw `session_chunk`), the status is `empty_db` (distillation hasn't run yet).

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
2. Read existing `hooks.UserPromptSubmit` array (may have user's own hooks)
3. Check if a Memor hook entry already exists (match by command path containing `memor-hook`)
4. If not present, append (not replace) the Memor hook entry to the array
5. If already present, update timeout/command path in place
6. Set timeout to 5000ms
7. Write back preserving all other settings and hooks
8. Print confirmation with the hook path

Must be idempotent — running twice doesn't duplicate. Must preserve existing user hooks.

### Shared Recall Core

**Problem:** `skill/recall.py` and the hook both implement recall logic independently. They'll drift.

**Solution:** Extract a shared `memor/recall.py` module:

```python
def recall(query: str, project: str, db_path: str, *, k: int = 8,
           threshold: float = 0.3) -> RecallResult:
    """Core recall function used by both hook and skill."""
```

`RecallResult` is a dataclass with: hits, status, tokens_injected, latency_ms, status_message.

Both `skill/recall.py` and `bin/memor-hook.py` call this function. One implementation, one place to fix bugs.

**Files:**
- Create: `memor/recall.py` — shared recall core
- Modify: `skill/recall.py` — delegate to `memor/recall.py`
- `bin/memor-hook.py` — uses `memor/recall.py`

### Files Created/Modified (Section 2 total)

- Create: `memor/project.py` — canonical project resolver
- Create: `memor/recall.py` — shared recall core
- Create: `memor/hook_server.py` — warm sidecar daemon
- Create: `bin/memor-hook.py` — thin hook client
- Modify: `memor/cli.py` — add `install-hook` command
- Modify: `memor/daemon.py` — use shared project resolver
- Modify: `memor/store/sqlite_store.py` — add `recall_log` table, `log_recall()`, `get_recall_stats()` methods
- Modify: `skill/recall.py` — delegate to shared recall core

---

## 3. Web Dashboard

### Overview

A local web dashboard served by FastAPI that visualizes Memor's production metrics. Replaces the TUI inspector as the primary UI.

### Architecture

- `memor/dashboard/server.py` — FastAPI app with JSON API endpoints
- `memor/dashboard/static/index.html` — single HTML file with inlined CSS, vanilla JS
- CLI command `memor dashboard` starts uvicorn on `localhost:8420`, opens browser

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/summary` | Total recalls, tokens injected, avg latency, hit rate, active project count |
| `GET` | `/api/projects` | Per-project: recall count, tokens injected, avg score, status breakdown |
| `GET` | `/api/recalls?limit=50&project=X` | Recent recall events, filterable by project |
| `GET` | `/api/savings` | Per-project: tokens recalled vs full project context size, % reduction, avg relevance score |
| `GET` | `/api/health` | Daemon status, DB file size, artifact counts by kind, last ingest timestamp, embedder dimension |

All endpoints read from `recall_log` and `artifacts` tables in `~/.memor/memor.db`.

### Dashboard Layout

Single-page app with 4 sections, top to bottom:

**1. Hero Metrics (top row of cards)**
- Total recalls (count)
- Tokens injected (sum)
- Hit rate (% of recalls with hits > 0)
- Avg relevance score (mean top_score across all recalls with hits)
- Avg latency (ms)
- Projects tracked (count)

Big numbers in colored cards. First thing the user sees.

**2. Recall Quality + Efficiency (headline section)**
Per-project display showing two paired metrics:
- **Efficiency:** tokens recalled vs full project context size, % reduction
- **Relevance:** average top similarity score for this project's recalls

The efficiency number alone is vanity math — you could return 1 random token and claim 99.99% reduction. Pairing it with relevance score gives an honest picture: "Memor sent 0.6% of available context, and the average relevance was 0.72." If efficiency is high but relevance is low, something is wrong.

A note on the dashboard: "Token savings estimates how much context Memor selectively recalled vs. sending everything. Relevance score measures how well the recalled context matched your query. Both matter."

**3. Project Breakdown (sortable table)**
Columns: project name, recall count, total tokens injected, avg top score, ok/no_hits/extractive_only counts. Sortable by clicking column headers. Click a row to filter the recalls table below.

**4. Recent Recalls (table)**
Columns: timestamp, project, query preview (first 80 chars), hits, top score, tokens, latency, status badge.
- Status badges: green (ok), yellow (extractive_only), gray (no_hits)
- Auto-refreshes every 30s via `setInterval` + `fetch()`
- Filterable by project (from clicking project table above, or a dropdown)

### Styling: No CDN Dependency

**Problem:** Tailwind CSS via CDN means the dashboard doesn't work offline or on restricted networks.

**Solution:** Inline CSS in the HTML file. Use a minimal, hand-written CSS approach:
- CSS custom properties for theming (dark/light mode)
- CSS grid for the card layout
- A small set of utility classes (~200 lines of CSS) inlined in a `<style>` tag
- No external dependencies — the HTML file is fully self-contained

This keeps the dashboard working offline, on air-gapped machines, and in restricted corporate environments. The CSS is small enough (~5KB) that inlining it is cheaper than a network request.

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
- `memor/dashboard/static/index.html` — complete dashboard UI (self-contained, no CDN)

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

## 5. Infrastructure Fixes

### 5.1 SQLite WAL Mode

**Problem:** The daemon writes to the DB while the hook reads from it. SQLite's default journal mode blocks readers during writes. During an ingestion burst (810 files), the hook could get locked out and timeout.

**Solution:** Enable WAL mode in `SqliteStore.__init__`:

```python
self.db.execute("PRAGMA journal_mode=WAL")
```

WAL (Write-Ahead Logging) allows concurrent reads and writes. One writer can proceed while multiple readers access the DB simultaneously. This is the standard configuration for any SQLite database with concurrent access.

### 5.2 Embedding Dimension Safety

**Problem:** If someone starts with API embeddings (dim=1536) then switches to local ONNX (dim=384), or vice versa, the existing vectors are the wrong dimension. `sqlite-vec` will either crash or return garbage results. This is a silent corruption with no error message.

**Solution:** Store the embedder dimension in a metadata table and check on startup:

```sql
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
```

On `SqliteStore.__init__`:
1. Check if `meta` table has a `dim` entry
2. If yes and it doesn't match the current embedder's `dim` → raise a clear error: "Database was created with dim=1536 but current embedder has dim=384. Either use the same embedder or re-ingest with `memor re-ingest`."
3. If no entry → write the current dim (first-time setup)

This catches the mismatch immediately with a clear error instead of silently corrupting results.

### 5.3 Cold-Start Experience

**Problem:** New user installs, runs `install-hook`, types a prompt. DB is empty. They see "run memor daemon." They start the daemon. But when does Memor actually start being useful?

**Solution:** Clear timeline communicated during `install-hook` and in the dashboard health endpoint:

1. `memor install-hook` prints a getting-started message:
   ```
   Hook installed. Next steps:
   1. Start the daemon: memor daemon
      (First run ingests existing sessions — takes ~2-5 minutes for a typical setup)
   2. The daemon runs extractive distillation automatically (no API key needed)
   3. For richer memories, set ANTHROPIC_API_KEY for LLM-powered distillation
   4. Open the dashboard to track progress: memor dashboard
   ```

2. The daemon prints progress during first ingestion: "Ingesting: 142/810 files (17%)..."

3. The `/api/health` endpoint returns an `onboarding_status` field:
   - `"no_data"` — no artifacts, daemon hasn't run
   - `"ingesting"` — artifacts exist but no memories yet (daemon running, distillation pending)
   - `"extractive"` — extractive memories exist, LLM distillation not configured
   - `"full"` — LLM-distilled memories present
   
   The dashboard renders a progress banner for non-`"full"` states, guiding the user through setup.

### Files Modified (Section 5)

- `memor/store/sqlite_store.py` — WAL mode pragma, `meta` table for dimension tracking, dimension check on init

---

## Testing Strategy

### New Tests

- `tests/test_noise_filter.py` — test whitelist-based signal scoring with real-world patterns (tool outputs, system-reminders, base64, decisions, lessons)
- `tests/test_project_resolver.py` — test project resolution: git roots, monorepos, home dir, fallback
- `tests/test_hook.py` — test hook client: mock stdin/stdout, verify JSON contract, test all status scenarios, test graceful error handling
- `tests/test_hook_server.py` — test sidecar: startup, socket communication, auto-shutdown on inactivity
- `tests/test_recall_core.py` — test shared `recall()` function: threshold filtering, status detection, result formatting
- `tests/test_recall_log.py` — test `log_recall()` and `get_recall_stats()` store methods
- `tests/test_dashboard.py` — test FastAPI endpoints with `TestClient`, verify JSON shapes, test empty DB edge case, test onboarding_status
- `tests/test_install_hook.py` — test settings.json modification (idempotency, existing hooks preserved, merging with user hooks)
- `tests/test_dimension_safety.py` — test dimension mismatch detection and error messaging

### Existing Tests

All 55 existing tests continue to pass (minus `test_tui.py` which is removed).

---

## Task Breakdown

1. Infrastructure fixes: WAL mode, dimension safety, meta table
2. Whitelist-based noise filter + battle-test on real transcripts
3. Canonical project resolver (`memor/project.py`)
4. Shared recall core (`memor/recall.py`)
5. Recall log table and store methods
6. Hook sidecar server (`memor/hook_server.py`)
7. Hook client script (`bin/memor-hook.py`)
8. `install-hook` CLI command with cold-start messaging
9. FastAPI dashboard server + API endpoints
10. Dashboard HTML (self-contained, no CDN)
11. `dashboard` CLI command
12. Remove TUI, update deps
13. Update `skill/recall.py` to use shared core
14. Tests for all new components
