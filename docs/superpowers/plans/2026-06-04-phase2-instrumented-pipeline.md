# Phase 2: Instrumented Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Memor's value visible and automatic — battle-tested pipeline, seamless recall hook, and a web dashboard showing production metrics.

**Architecture:** Three layers: (1) hardened SQLite store with WAL + dimension safety, (2) Claude Code hook with warm sidecar for automatic recall injection, (3) FastAPI web dashboard for metrics. TUI removed; web dashboard is the primary UI.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, sqlite-vec, httpx, typer, inline CSS + vanilla JS

---

## File Map

### New files
| File | Responsibility |
|---|---|
| `memor/project.py` | Canonical project name resolution (git root walk-up) |
| `memor/recall.py` | Shared recall core used by hook + skill |
| `memor/hook_server.py` | Unix socket sidecar keeping embedder warm |
| `bin/memor-hook.py` | Thin hook client for Claude Code `UserPromptSubmit` |
| `memor/dashboard/__init__.py` | Package init |
| `memor/dashboard/server.py` | FastAPI app + JSON API endpoints |
| `memor/dashboard/static/index.html` | Self-contained dashboard HTML (inline CSS, vanilla JS) |
| `tests/test_dimension_safety.py` | Tests for meta table + dimension checks |
| `tests/test_noise_filter.py` | Tests for whitelist noise filter |
| `tests/test_project_resolver.py` | Tests for project resolution logic |
| `tests/test_recall_core.py` | Tests for shared recall function |
| `tests/test_recall_log.py` | Tests for recall_log store methods |
| `tests/test_hook.py` | Tests for hook client JSON contract |
| `tests/test_hook_server.py` | Tests for sidecar startup + socket comms |
| `tests/test_dashboard.py` | Tests for FastAPI endpoints via TestClient |
| `tests/test_install_hook.py` | Tests for settings.json modification |

### Modified files
| File | Changes |
|---|---|
| `memor/store/sqlite_store.py` | WAL mode, meta table, dimension check, recall_log table, `log_recall()`, `get_recall_stats()`, `get_project_stats()`, `get_onboarding_status()` |
| `memor/ingest/claude_code.py` | Replace `_is_noise` with `_signal_score` whitelist, strip system-reminder tags, dedup by hash |
| `memor/daemon.py` | Use shared project resolver, add progress counter |
| `memor/cli.py` | Add `install-hook` + `dashboard` commands, remove `inspector` command |
| `skill/recall.py` | Delegate to `memor/recall.py` |
| `pyproject.toml` | Remove `textual`, add `fastapi`, `uvicorn` |

### Deleted files
| File | Reason |
|---|---|
| `memor/tui/__init__.py` | TUI removed |
| `memor/tui/app.py` | TUI removed |
| `memor/tui/screens/__init__.py` | TUI removed |
| `memor/tui/screens/query.py` | TUI removed |
| `memor/tui/screens/browse.py` | TUI removed |
| `memor/tui/screens/eval_screen.py` | TUI removed |
| `memor/tui/screens/edges.py` | TUI removed |
| `tests/test_tui.py` | TUI removed |
| `inspector.py` | TUI removed |

---

### Task 1: Remove TUI + Update Dependencies

**Files:**
- Delete: `memor/tui/` (entire directory), `tests/test_tui.py`, `inspector.py`
- Modify: `memor/cli.py:208-216` (remove `inspector` command)
- Modify: `pyproject.toml:6,11` (swap textual for fastapi+uvicorn)

- [ ] **Step 1: Delete TUI files**

```bash
rm -rf memor/tui/
rm tests/test_tui.py
rm inspector.py
```

- [ ] **Step 2: Remove inspector command from cli.py**

Remove these lines from `memor/cli.py`:

```python
@app.command("inspector")
def inspector_cmd(db: str = "memor.db", fake: bool = False):
    """Launch the TUI inspector."""
    from memor.tui.app import MemorApp
    typer.echo("Loading embedder and database...")
    e = _embedder(fake)
    s = SqliteStore(_db_path(db), dim=e.dim)
    tui = MemorApp(db_path=db, store=s, embedder=e)
    tui.run()
```

- [ ] **Step 3: Update pyproject.toml dependencies**

Replace `"textual>=0.80"` with `"fastapi>=0.111"` and `"uvicorn>=0.30"` in the dependencies list:

```toml
dependencies = [
  "sqlite-vec>=0.1.6",
  "numpy>=1.26",
  "typer>=0.12",
  "httpx>=0.27",
  "tiktoken>=0.7",
  "fastapi>=0.111",
  "uvicorn>=0.30",
]
```

- [ ] **Step 4: Reinstall and run tests**

Run: `.venv/bin/pip install -e ".[dev]" && .venv/bin/python -m pytest tests/ -v`
Expected: All tests pass (54 tests — down from 55 after removing test_tui.py)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove TUI, swap textual for fastapi+uvicorn"
```

---

### Task 2: Infrastructure — WAL Mode + Meta Table + Dimension Safety

**Files:**
- Modify: `memor/store/sqlite_store.py:10-19` (constructor), `memor/store/sqlite_store.py:21-35` (schema)
- Create: `tests/test_dimension_safety.py`

- [ ] **Step 1: Write failing tests for dimension safety**

Create `tests/test_dimension_safety.py`:

```python
import pytest
from memor.store.sqlite_store import SqliteStore


def test_meta_table_stores_dim(tmp_path):
    db = str(tmp_path / "m.db")
    s = SqliteStore(db, dim=16)
    row = s.db.execute("SELECT value FROM meta WHERE key='dim'").fetchone()
    assert row is not None
    assert row["value"] == "16"


def test_dimension_mismatch_raises(tmp_path):
    db = str(tmp_path / "m.db")
    SqliteStore(db, dim=16)
    with pytest.raises(SystemExit, match="dim=16.*dim=384"):
        SqliteStore(db, dim=384)


def test_same_dimension_reopens_fine(tmp_path):
    db = str(tmp_path / "m.db")
    SqliteStore(db, dim=16)
    s2 = SqliteStore(db, dim=16)
    assert s2.dim == 16


def test_wal_mode_enabled(tmp_path):
    db = str(tmp_path / "m.db")
    s = SqliteStore(db, dim=16)
    mode = s.db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_recall_log_table_exists(tmp_path):
    db = str(tmp_path / "m.db")
    s = SqliteStore(db, dim=16)
    s.db.execute("SELECT COUNT(*) FROM recall_log").fetchone()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dimension_safety.py -v`
Expected: FAIL — no `meta` table, no WAL mode, no `recall_log` table

- [ ] **Step 3: Implement store changes**

Replace the `__init__` and `_init_schema` methods in `memor/store/sqlite_store.py`:

```python
class SqliteStore:
    def __init__(self, path: str, dim: int):
        self.dim = dim
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self._init_schema()
        self._check_dim(dim)

    def _init_schema(self):
        self.db.executescript(f"""
        CREATE TABLE IF NOT EXISTS artifacts(
          id TEXT PRIMARY KEY, kind TEXT, project TEXT, source TEXT,
          text TEXT, token_count INTEGER, created_at REAL, meta TEXT,
          active INTEGER DEFAULT 1, superseded_by TEXT);
        CREATE TABLE IF NOT EXISTS edges(
          src_id TEXT, dst_id TEXT, type TEXT,
          PRIMARY KEY(src_id, dst_id, type));
        CREATE INDEX IF NOT EXISTS idx_art_project ON artifacts(project, active);
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_artifacts USING vec0(
          embedding float[{self.dim}]);
        CREATE TABLE IF NOT EXISTS eval_runs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL, config TEXT, metrics TEXT);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS recall_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp REAL, project TEXT, query_preview TEXT,
          hits_count INTEGER, top_score REAL, tokens_injected INTEGER,
          latency_ms REAL, status TEXT, session_id TEXT);
        """)
        self.db.commit()

    def _check_dim(self, dim: int):
        row = self.db.execute("SELECT value FROM meta WHERE key='dim'").fetchone()
        if row is None:
            self.db.execute("INSERT INTO meta(key, value) VALUES('dim', ?)", (str(dim),))
            self.db.commit()
        elif int(row["value"]) != dim:
            raise SystemExit(
                f"Embedding dimension mismatch: database was created with dim={row['value']} "
                f"but current embedder has dim={dim}. Use the same embedder or re-ingest."
            )
```

- [ ] **Step 4: Add recall log helper methods**

Add to `SqliteStore` class in `memor/store/sqlite_store.py`:

```python
    def log_recall(self, project: str, query_preview: str, hits_count: int,
                   top_score: float, tokens_injected: int, latency_ms: float,
                   status: str, session_id: str = "") -> None:
        import time as _time
        self.db.execute(
            "INSERT INTO recall_log(timestamp,project,query_preview,hits_count,"
            "top_score,tokens_injected,latency_ms,status,session_id) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (_time.time(), project, query_preview[:100], hits_count, top_score,
             tokens_injected, latency_ms, status, session_id))
        self.db.commit()

    def get_recall_stats(self) -> dict:
        r = self.db.execute("""
            SELECT COUNT(*) as total,
                   SUM(tokens_injected) as tokens,
                   AVG(latency_ms) as avg_latency,
                   SUM(CASE WHEN hits_count > 0 THEN 1 ELSE 0 END) as with_hits
            FROM recall_log
        """).fetchone()
        total = r["total"] or 0
        return {
            "total_recalls": total,
            "total_tokens": r["tokens"] or 0,
            "avg_latency_ms": round(r["avg_latency"] or 0, 1),
            "hit_rate": round((r["with_hits"] or 0) / total, 3) if total > 0 else 0,
        }

    def get_project_stats(self) -> list[dict]:
        rows = self.db.execute("""
            SELECT project,
                   COUNT(*) as recalls,
                   SUM(tokens_injected) as tokens,
                   AVG(CASE WHEN hits_count > 0 THEN top_score END) as avg_score,
                   SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) as ok_count,
                   SUM(CASE WHEN status='no_hits' THEN 1 ELSE 0 END) as no_hits_count,
                   SUM(CASE WHEN status='extractive_only' THEN 1 ELSE 0 END) as extractive_count
            FROM recall_log
            GROUP BY project
            ORDER BY recalls DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def get_recent_recalls(self, limit: int = 50, project: str | None = None) -> list[dict]:
        if project:
            rows = self.db.execute(
                "SELECT * FROM recall_log WHERE project=? ORDER BY timestamp DESC LIMIT ?",
                (project, limit)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM recall_log ORDER BY timestamp DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_onboarding_status(self) -> str:
        chunks = self.db.execute(
            "SELECT COUNT(*) as c FROM artifacts WHERE kind='session_chunk' AND active=1"
        ).fetchone()["c"]
        if chunks == 0:
            return "no_data"
        mems = self.db.execute(
            "SELECT COUNT(*) as c FROM artifacts WHERE kind='memory' AND active=1"
        ).fetchone()["c"]
        if mems == 0:
            return "ingesting"
        llm_mems = self.db.execute("""
            SELECT COUNT(*) as c FROM artifacts
            WHERE kind='memory' AND active=1
              AND json_extract(meta, '$.mem_type') IN ('decision','lesson','snippet','bugfix')
        """).fetchone()["c"]
        if llm_mems > 0:
            return "full"
        return "extractive"
```

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass (54 existing + 5 new = 59)

- [ ] **Step 6: Commit**

```bash
git add memor/store/sqlite_store.py tests/test_dimension_safety.py
git commit -m "feat: add WAL mode, dimension safety, recall_log table to store"
```

---

### Task 3: Whitelist Noise Filter

**Files:**
- Modify: `memor/ingest/claude_code.py`
- Create: `tests/test_noise_filter.py`

- [ ] **Step 1: Write failing tests for noise filter**

Create `tests/test_noise_filter.py`:

```python
from memor.ingest.claude_code import _signal_score, _strip_system_reminders, parse_transcript
from pathlib import Path
import json


def test_user_question_scores_positive():
    assert _signal_score("How does the retriever handle edge expansion?", "user", 12) > 0


def test_assistant_decision_scores_positive():
    text = "We decided to use argon2 instead of bcrypt because it's memory-hard."
    assert _signal_score(text, "assistant", 15) > 0


def test_assistant_bugfix_scores_positive():
    text = "The root cause was a race condition in the token refresh loop."
    assert _signal_score(text, "assistant", 14) > 0


def test_assistant_lesson_scores_positive():
    text = "Always use WAL mode when you have concurrent readers and writers on SQLite."
    assert _signal_score(text, "assistant", 15) > 0


def test_assistant_code_rationale_scores_positive():
    text = "We use a Unix socket instead of TCP because it avoids port conflicts:\n```python\nserver = await asyncio.start_unix_server(handle, path)\n```"
    assert _signal_score(text, "assistant", 25) > 0


def test_short_filler_scores_zero():
    assert _signal_score("Let me check that for you.", "assistant", 6) == 0


def test_very_short_text_scores_zero():
    assert _signal_score("OK", "assistant", 1) == 0


def test_base64_blob_scores_zero():
    blob = "A" * 300
    assert _signal_score(blob, "assistant", 75) == 0


def test_file_listing_scores_zero():
    text = "/src/main.py\n/src/utils.py\n/tests/test_main.py\n/README.md"
    assert _signal_score(text, "assistant", 8) == 0


def test_permission_prompt_scores_zero():
    assert _signal_score("Allow Read access to /src/main.py?", "user", 8) == 0


def test_strip_system_reminders():
    text = "Hello <system-reminder>secret stuff</system-reminder> world"
    assert _strip_system_reminders(text) == "Hello  world"


def test_strip_system_reminders_nothing_left():
    text = "<system-reminder>only this</system-reminder>"
    assert _strip_system_reminders(text).strip() == ""


def test_parse_transcript_deduplicates(tmp_path):
    lines = [
        '{"type":"user","timestamp":"2026-05-01T10:00:00Z","message":{"role":"user","content":"fix the auth refresh loop in the login handler"}}',
        '{"type":"user","timestamp":"2026-05-01T10:00:01Z","message":{"role":"user","content":"fix the auth refresh loop in the login handler"}}',
        '{"type":"assistant","timestamp":"2026-05-01T10:00:05Z","message":{"role":"assistant","content":[{"type":"text","text":"The root cause was a race condition in the token refresh. Here is the fix for the auth handler."}]}}',
    ]
    f = tmp_path / "sess.jsonl"
    f.write_text("\n".join(lines))
    arts = parse_transcript(f, project="test", filter_noise=True)
    user_texts = [a.text for a in arts if a.meta.get("role") == "user"]
    assert len(user_texts) <= 1


def test_parse_transcript_skips_non_user_assistant(tmp_path):
    lines = [
        '{"type":"system","timestamp":"2026-05-01T10:00:00Z","message":{"role":"system","content":"system prompt"}}',
        '{"type":"attachment","timestamp":"2026-05-01T10:00:01Z","attachment":{"type":"hook"}}',
        '{"type":"user","timestamp":"2026-05-01T10:00:02Z","message":{"role":"user","content":"How does the retriever handle edge expansion in the graph?"}}',
    ]
    f = tmp_path / "sess.jsonl"
    f.write_text("\n".join(lines))
    arts = parse_transcript(f, project="test", filter_noise=True)
    assert len(arts) == 1
    assert arts[0].meta["role"] == "user"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_noise_filter.py -v`
Expected: FAIL — `_signal_score` and `_strip_system_reminders` don't exist yet

- [ ] **Step 3: Implement whitelist noise filter**

Replace the contents of `memor/ingest/claude_code.py`:

```python
from __future__ import annotations
import hashlib, json, re
from datetime import datetime
from pathlib import Path
from memor.types import Artifact
from memor.tokencount import count_tokens

def _epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()

def _text_of(message: dict) -> str:
    c = message.get("content", "")
    if isinstance(c, str):
        return c
    parts = []
    for block in c:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)

def _strip_system_reminders(text: str) -> str:
    return _SYSTEM_REMINDER_RE.sub("", text)


_DECISION_RE = re.compile(
    r"(we decided|the approach is|instead of|switched to|chose .+ over|"
    r"trade-?off|architecture:|design decision)", re.I)
_BUGFIX_RE = re.compile(
    r"(the fix is|root cause|the bug was|the issue was|caused by|"
    r"the problem is|this fails because|the error occurs)", re.I)
_LESSON_RE = re.compile(
    r"(always use|never use|never do|important:|note:|pattern:|"
    r"best practice|lesson learned|rule of thumb|should always|should never)", re.I)
_FILLER_STARTS = re.compile(
    r"^(Let me |Now let me |Now I|I'll |I will |Good[,.]|Great[,.]|Perfect[!,.]|Done[!,.]"
    r"|Alright|Sure[,.]|OK[,.]|Moving to |Looking at |Checking )", re.I)
_BASE64_RE = re.compile(r"[A-Za-z0-9+/=]{200,}")
_FILE_LISTING_RE = re.compile(r"^(\s*[-/\w.]+\n){3,}$")
_PERMISSION_RE = re.compile(r"^(Allow |Permission |Approve |Deny )", re.I)
_SKILL_BOILERPLATE = "Base directory for this skill"

MIN_SIGNAL_TOKENS = 8


def _signal_score(text: str, role: str, token_count: int) -> float:
    if token_count < MIN_SIGNAL_TOKENS:
        return 0
    if _SKILL_BOILERPLATE in text:
        return 0
    if _BASE64_RE.search(text):
        return 0
    if _PERMISSION_RE.match(text):
        return 0
    if _FILE_LISTING_RE.match(text):
        return 0
    if _FILLER_STARTS.match(text) and token_count < 30:
        return 0

    score = 0.0
    if role == "user" and token_count >= 20:
        score += 1.0
    if role == "assistant":
        if _DECISION_RE.search(text):
            score += 2.0
        if _BUGFIX_RE.search(text):
            score += 2.0
        if _LESSON_RE.search(text):
            score += 2.0
        if "```" in text and token_count >= 40:
            score += 1.0
        if token_count >= 100 and score == 0:
            score += 0.5
    return score


def parse_transcript(path: Path, project: str, *, filter_noise: bool = True) -> list[Artifact]:
    session_id = path.stem
    arts: list[Artifact] = []
    seen_hashes: set[str] = set()
    for i, line in enumerate(path.read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("type") not in ("user", "assistant"):
            continue
        msg = rec.get("message", {})
        text = _text_of(msg).strip()
        if not text:
            continue
        text = _strip_system_reminders(text).strip()
        if not text:
            continue
        token_count = max(1, count_tokens(text))
        if filter_noise:
            role = msg.get("role", "")
            if _signal_score(text, role, token_count) <= 0:
                continue
            text_hash = hashlib.md5(text.encode()).hexdigest()
            if text_hash in seen_hashes:
                continue
            seen_hashes.add(text_hash)
        arts.append(Artifact(
            id=f"{session_id}:{i}", kind="session_chunk", project=project,
            source="claude_code", text=text, token_count=token_count,
            created_at=_epoch(rec["timestamp"]),
            meta={"session_id": session_id, "role": msg.get("role"), "ord": i}))
    return arts

def discover_project(transcript_path: Path) -> str:
    return transcript_path.parent.name
```

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass. The existing `test_ingest_claude_code.py` should still pass because `parse_transcript` with `filter_noise=False` bypasses the new filter, and the sample fixture text ("fix the auth refresh loop" and "The loop is caused by re-issuing the token on 401") should score positively with `filter_noise=True` anyway.

- [ ] **Step 5: Commit**

```bash
git add memor/ingest/claude_code.py tests/test_noise_filter.py
git commit -m "feat: replace blacklist noise filter with whitelist signal scoring"
```

---

### Task 4: Canonical Project Resolver

**Files:**
- Create: `memor/project.py`
- Create: `tests/test_project_resolver.py`
- Modify: `memor/daemon.py:23-33`

- [ ] **Step 1: Write failing tests**

Create `tests/test_project_resolver.py`:

```python
from memor.project import resolve_project, decode_claude_dir


def test_resolve_project_with_git_root(tmp_path):
    repo = tmp_path / "my-project"
    repo.mkdir()
    (repo / ".git").mkdir()
    sub = repo / "src" / "lib"
    sub.mkdir(parents=True)
    assert resolve_project(str(sub)) == "my-project"


def test_resolve_project_at_git_root(tmp_path):
    repo = tmp_path / "foo-bar"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert resolve_project(str(repo)) == "foo-bar"


def test_resolve_project_no_git_fallback(tmp_path):
    d = tmp_path / "some-dir"
    d.mkdir()
    assert resolve_project(str(d)) == "some-dir"


def test_resolve_project_home_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_project(str(tmp_path)) == tmp_path.name


def test_decode_claude_dir_simple():
    assert decode_claude_dir("-Users-nimit-Documents-Projects-plirin") == "/Users/nimit/Documents/Projects/plirin"


def test_decode_claude_dir_nested():
    assert decode_claude_dir("-Users-nimit-Documents-Eukarya-reearth-flow") == "/Users/nimit/Documents/Eukarya/reearth-flow"


def test_resolve_from_claude_dir(tmp_path):
    repo = tmp_path / "reearth-flow"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert resolve_project(str(repo)) == "reearth-flow"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_project_resolver.py -v`
Expected: FAIL — `memor.project` doesn't exist

- [ ] **Step 3: Implement project resolver**

Create `memor/project.py`:

```python
from __future__ import annotations
from pathlib import Path


def resolve_project(cwd: str) -> str:
    p = Path(cwd).resolve()
    git_root = _find_git_root(p)
    if git_root:
        return git_root.name
    return p.name


def _find_git_root(path: Path) -> Path | None:
    current = path
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def decode_claude_dir(dirname: str) -> str:
    """Decode a Claude projects directory name back to a filesystem path.

    '-Users-nimit-Documents-Projects-plirin' -> '/Users/nimit/Documents/Projects/plirin'

    Claude encodes paths by replacing '/' with '-'. We reconstruct by
    recognizing that the path starts with '/' (leading dash) and common
    path segments like 'Users', 'home', etc.
    """
    stripped = dirname.lstrip("-")
    parts = stripped.split("-")
    return "/" + "/".join(parts)


def resolve_project_from_claude_dir(dirname: str) -> str:
    """Resolve project name from a Claude projects directory name."""
    decoded = decode_claude_dir(dirname)
    return resolve_project(decoded)
```

- [ ] **Step 4: Update daemon to use shared resolver**

In `memor/daemon.py`, replace `_project_name_from_dir`:

```python
from memor.project import resolve_project_from_claude_dir

def _project_name_from_dir(dirname: str) -> str:
    """Derive a clean project name from a Claude projects directory name."""
    return resolve_project_from_claude_dir(dirname)
```

Also add the import at the top of the file and remove the old inline implementation.

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass. The existing daemon tests (`test_project_name_simple`, etc.) will need updating because the resolver now decodes the full path and uses the git root. Since the tests don't have `.git` directories, they'll fall back to the last component — but `decode_claude_dir` returns a different last component than the old logic for some cases.

Check: if `test_project_name_nested` fails (expects `"flow"` but `decode_claude_dir` + `resolve_project` returns `"reearth-flow"` since no `.git` exists), update the test expectation to match the new correct behavior:

```python
def test_project_name_nested():
    # Without a .git dir, resolve_project falls back to last path component
    # decode_claude_dir("-Users-nimit-Documents-Eukarya-reearth-flow") 
    # = "/Users/nimit/Documents/Eukarya/reearth-flow" -> last component = "reearth-flow"
    assert _project_name_from_dir("-Users-nimit-Documents-Eukarya-reearth-flow") == "reearth-flow"
```

Similarly update `test_project_name_worktree`:
```python
def test_project_name_worktree():
    assert _project_name_from_dir(
        "-Users-nimit-Documents-Eukarya-ygo--claude-worktrees-musing-haibt-701a57"
    ) == "701a57"
```

This one: `decode_claude_dir` will produce `/Users/nimit/Documents/Eukarya/ygo/-claude-worktrees/musing-haibt/701a57` — the dash-encoding is ambiguous for paths with actual dashes. We need to handle the decode more carefully. Since Claude dir names are ambiguous (dashes could be path separators or literal dashes), and the git root approach solves the real problem, the simplest fix is: **keep the old `_project_name_from_dir` logic for daemon directory scanning** (where we don't have a real filesystem path), and use `resolve_project(cwd)` for the hook (where we have a real CWD).

Revise `memor/daemon.py` to keep its simple last-component extraction:

```python
def _project_name_from_dir(dirname: str) -> str:
    """Derive a clean project name from a Claude projects directory name.
    Uses last component of the dash-encoded path."""
    parts = dirname.strip("-").split("-")
    return parts[-1] if parts else dirname
```

And `memor/project.py` provides `resolve_project(cwd)` for the hook, which walks up to the git root. They'll agree for simple cases (both return the repo name), and the hook is more accurate for nested/monorepo cases.

- [ ] **Step 6: Run all tests again**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add memor/project.py tests/test_project_resolver.py memor/daemon.py
git commit -m "feat: add canonical project resolver with git root walk-up"
```

---

### Task 5: Shared Recall Core

**Files:**
- Create: `memor/recall.py`
- Create: `tests/test_recall_core.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_recall_core.py`:

```python
from memor.recall import recall, RecallResult
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


def _seed_store(tmp_path, project="testproj"):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    arts = [
        Artifact(id="a1", kind="memory", project=project, source="distill",
                 text="we decided to use argon2 for password hashing in the auth module",
                 token_count=12, created_at=100.0,
                 meta={"mem_type": "decision", "session_id": "s1"}),
        Artifact(id="a2", kind="memory", project=project, source="distill",
                 text="the root cause of the login bug was a race condition in token refresh",
                 token_count=14, created_at=200.0,
                 meta={"mem_type": "bugfix", "session_id": "s2"}),
    ]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    return s, e


def test_recall_returns_hits(tmp_path):
    s, e = _seed_store(tmp_path)
    result = recall("password hashing", "testproj", str(tmp_path / "m.db"),
                    embedder=e, k=8, threshold=0.0)
    assert isinstance(result, RecallResult)
    assert result.hits_count > 0
    assert result.status == "ok"
    assert result.tokens_injected > 0


def test_recall_threshold_filters_low_scores(tmp_path):
    s, e = _seed_store(tmp_path)
    result = recall("completely unrelated quantum physics topic", "testproj",
                    str(tmp_path / "m.db"), embedder=e, k=8, threshold=0.99)
    assert result.status == "no_hits"
    assert result.hits_count == 0


def test_recall_empty_project(tmp_path):
    e = FakeEmbedder(dim=16)
    SqliteStore(str(tmp_path / "m.db"), dim=16)
    result = recall("anything", "nonexistent", str(tmp_path / "m.db"),
                    embedder=e, k=8, threshold=0.0)
    assert result.status == "no_hits"


def test_recall_no_db(tmp_path):
    e = FakeEmbedder(dim=16)
    result = recall("anything", "proj", str(tmp_path / "nope.db"), embedder=e)
    assert result.status == "empty_db"


def test_recall_extractive_only_status(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    art = Artifact(id="m1", kind="memory", project="p", source="distill",
                   text="extracted chunk about authentication patterns and security",
                   token_count=10, created_at=100.0,
                   meta={"mem_type": "extract", "session_id": "s1"})
    s.add_artifacts([art], e.embed([art.text]))
    result = recall("authentication", "p", str(tmp_path / "m.db"),
                    embedder=e, k=8, threshold=0.0)
    assert result.status == "extractive_only"


def test_recall_result_has_formatted_context(tmp_path):
    s, e = _seed_store(tmp_path)
    result = recall("password hashing", "testproj", str(tmp_path / "m.db"),
                    embedder=e, k=8, threshold=0.0)
    assert "## Recalled Memories" in result.formatted_context
    assert "Memor:" in result.status_message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_recall_core.py -v`
Expected: FAIL — `memor.recall` doesn't exist

- [ ] **Step 3: Implement shared recall core**

Create `memor/recall.py`:

```python
from __future__ import annotations
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from memor.types import Scope


@dataclass
class RecallResult:
    hits_count: int
    top_score: float
    tokens_injected: int
    latency_ms: float
    status: str
    status_message: str
    formatted_context: str


def _format_timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")


def _detect_status(store, project: str, hits_count: int) -> str:
    if hits_count > 0:
        llm_mems = store.db.execute("""
            SELECT COUNT(*) as c FROM artifacts
            WHERE kind='memory' AND project=? AND active=1
              AND json_extract(meta, '$.mem_type') IN ('decision','lesson','snippet','bugfix')
        """, (project,)).fetchone()["c"]
        if llm_mems > 0:
            return "ok"
        return "extractive_only"
    return "no_hits"


def _status_message(status: str, project: str, hits_count: int,
                    tokens: int, top_score: float) -> str:
    if status == "ok":
        return f"Memor: recalled {hits_count} memories ({tokens} tokens, {top_score:.2f} top score)"
    if status == "extractive_only":
        return (f"Memor: recalled {hits_count} memories "
                f"(extractive only — set ANTHROPIC_API_KEY for richer distillation)")
    if status == "no_hits":
        return f'Memor: no relevant memories for project "{project}" yet'
    if status == "empty_db":
        return 'Memor: memory store is empty — run "memor daemon" to start ingesting sessions'
    if status == "no_embedder":
        return "Memor: inactive — set OPENAI_API_KEY or pip install memor-ai[local] for memory recall"
    return f"Memor: status={status}"


def recall(query: str, project: str, db_path: str, *,
           embedder=None, k: int = 8, threshold: float = 0.3) -> RecallResult:
    t0 = time.perf_counter()

    if not Path(db_path).exists():
        ms = (time.perf_counter() - t0) * 1000
        return RecallResult(
            hits_count=0, top_score=0.0, tokens_injected=0,
            latency_ms=ms, status="empty_db",
            status_message=_status_message("empty_db", project, 0, 0, 0.0),
            formatted_context="")

    from memor.store.sqlite_store import SqliteStore
    from memor.retrieve.retriever import Retriever

    store = SqliteStore(db_path, dim=embedder.dim)
    retriever = Retriever(store, embedder, k=k)
    trace = retriever.query(query, Scope(project=project))

    hits = [h for h in trace.hits if h.score >= threshold]
    top_score = hits[0].score if hits else 0.0
    tokens = sum(h.artifact.token_count for h in hits)

    if not hits:
        status = "no_hits"
    else:
        status = _detect_status(store, project, len(hits))

    msg = _status_message(status, project, len(hits), tokens, top_score)

    lines = []
    if hits:
        lines.append(f"## Recalled Memories (project: {project})")
        lines.append("")
        for i, h in enumerate(hits, 1):
            a = h.artifact
            kind_tag = a.meta.get("mem_type", a.kind)
            text = a.text if len(a.text) <= 600 else a.text[:600] + "..."
            source_parts = []
            sid = a.meta.get("session_id")
            if sid:
                source_parts.append(f"session {sid[:8]}")
            source_parts.append(_format_timestamp(a.created_at))
            source = ", ".join(source_parts)
            lines.append(f"### {i}. [{kind_tag}] {text}")
            lines.append(f"Source: {source} | score: {h.score:.3f}")
            lines.append("")

    lines.append("---")
    lines.append(msg)
    formatted = "\n".join(lines)

    ms = (time.perf_counter() - t0) * 1000
    return RecallResult(
        hits_count=len(hits), top_score=top_score, tokens_injected=tokens,
        latency_ms=ms, status=status, status_message=msg,
        formatted_context=formatted)
```

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add memor/recall.py tests/test_recall_core.py
git commit -m "feat: add shared recall core with status detection and formatting"
```

---

### Task 6: Hook Sidecar Server

**Files:**
- Create: `memor/hook_server.py`
- Create: `tests/test_hook_server.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_hook_server.py`:

```python
import asyncio
import json
import socket
import os
from pathlib import Path
from memor.hook_server import handle_request, IDLE_TIMEOUT_S


def test_handle_request_returns_json(tmp_path):
    from memor.embed.fake import FakeEmbedder
    from memor.store.sqlite_store import SqliteStore
    from memor.types import Artifact

    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(id="a1", kind="memory", project="myproj", source="distill",
                   text="we decided to use argon2 for password hashing in the auth module",
                   token_count=12, created_at=100.0,
                   meta={"mem_type": "decision", "session_id": "s1"})
    s.add_artifacts([art], e.embed([art.text]))

    req = {"prompt": "password hashing", "cwd": "/tmp/myproj", "session_id": "test"}
    result = handle_request(req, db_path=db_path, embedder=e)
    assert "hookSpecificOutput" in result
    output = result["hookSpecificOutput"]
    assert output["hookEventName"] == "UserPromptSubmit"
    assert "additionalContext" in output


def test_handle_request_empty_db(tmp_path):
    from memor.embed.fake import FakeEmbedder
    db_path = str(tmp_path / "nope.db")
    e = FakeEmbedder(dim=16)
    req = {"prompt": "anything", "cwd": "/tmp/proj", "session_id": "test"}
    result = handle_request(req, db_path=db_path, embedder=e)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "empty" in ctx.lower() or "daemon" in ctx.lower()


def test_idle_timeout_is_set():
    assert IDLE_TIMEOUT_S == 600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_hook_server.py -v`
Expected: FAIL — `memor.hook_server` doesn't exist

- [ ] **Step 3: Implement hook sidecar server**

Create `memor/hook_server.py`:

```python
from __future__ import annotations
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

SOCK_PATH = Path.home() / ".memor" / "hook.sock"
PID_PATH = Path.home() / ".memor" / "hook.pid"
DEFAULT_DB = str(Path.home() / ".memor" / "memor.db")
IDLE_TIMEOUT_S = 600

_embedder = None
_last_activity = 0.0


def _get_embedder():
    global _embedder
    if _embedder is not None:
        return _embedder
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        from memor.embed.api import APIEmbedder
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        _embedder = APIEmbedder(base_url=base_url, api_key=api_key)
        return _embedder
    try:
        from memor.embed.local import LocalEmbedder
        _embedder = LocalEmbedder()
        return _embedder
    except ImportError:
        return None


def handle_request(req: dict, *, db_path: str = DEFAULT_DB,
                   embedder=None) -> dict:
    from memor.recall import recall
    from memor.project import resolve_project

    cwd = req.get("cwd", "")
    project = resolve_project(cwd) if cwd else "unknown"
    query = req.get("prompt", "")
    session_id = req.get("session_id", "")

    if embedder is None:
        embedder = _get_embedder()
    if embedder is None:
        from memor.recall import _status_message
        msg = _status_message("no_embedder", project, 0, 0, 0.0)
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": f"---\n{msg}",
            }
        }

    result = recall(query, project, db_path, embedder=embedder, k=8, threshold=0.3)

    if Path(db_path).exists():
        try:
            from memor.store.sqlite_store import SqliteStore
            store = SqliteStore(db_path, dim=embedder.dim)
            store.log_recall(
                project=project, query_preview=query[:100],
                hits_count=result.hits_count, top_score=result.top_score,
                tokens_injected=result.tokens_injected, latency_ms=result.latency_ms,
                status=result.status, session_id=session_id)
        except Exception:
            pass

    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": result.formatted_context,
        }
    }


async def _handle_client(reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter) -> None:
    global _last_activity
    _last_activity = time.time()
    try:
        data = await asyncio.wait_for(reader.read(1_000_000), timeout=10)
        req = json.loads(data.decode())
        resp = handle_request(req)
        writer.write(json.dumps(resp).encode())
        await writer.drain()
    except Exception as e:
        err = json.dumps({"error": str(e)})
        writer.write(err.encode())
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def _idle_watchdog():
    global _last_activity
    while True:
        await asyncio.sleep(60)
        if time.time() - _last_activity > IDLE_TIMEOUT_S:
            _cleanup()
            os._exit(0)


def _cleanup():
    if SOCK_PATH.exists():
        SOCK_PATH.unlink()
    if PID_PATH.exists():
        PID_PATH.unlink()


async def serve(sock_path: str = str(SOCK_PATH)) -> None:
    global _last_activity
    _last_activity = time.time()
    p = Path(sock_path)
    if p.exists():
        p.unlink()
    p.parent.mkdir(parents=True, exist_ok=True)

    _get_embedder()

    server = await asyncio.start_unix_server(_handle_client, path=sock_path)
    PID_PATH.write_text(str(os.getpid()))
    asyncio.create_task(_idle_watchdog())

    async with server:
        await server.serve_forever()


def main():
    signal.signal(signal.SIGTERM, lambda *_: (_cleanup(), sys.exit(0)))
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        _cleanup()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add memor/hook_server.py tests/test_hook_server.py
git commit -m "feat: add hook sidecar server with idle auto-shutdown"
```

---

### Task 7: Hook Client Script

**Files:**
- Create: `bin/memor-hook.py`
- Create: `tests/test_hook.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_hook.py`:

```python
import json
import subprocess
import sys
from pathlib import Path


def test_hook_outputs_valid_json(tmp_path):
    from memor.embed.fake import FakeEmbedder
    from memor.store.sqlite_store import SqliteStore
    from memor.types import Artifact

    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(id="a1", kind="memory", project="myproj", source="distill",
                   text="we decided to use argon2 for password hashing in the auth module",
                   token_count=12, created_at=100.0,
                   meta={"mem_type": "decision", "session_id": "s1"})
    s.add_artifacts([art], e.embed([art.text]))

    from memor.hook_server import handle_request
    req = {"prompt": "password hashing", "cwd": str(tmp_path / "myproj"),
           "session_id": "test-session"}
    result = handle_request(req, db_path=db_path, embedder=e)
    output = json.dumps(result)
    parsed = json.loads(output)
    assert "hookSpecificOutput" in parsed
    assert parsed["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert isinstance(parsed["hookSpecificOutput"]["additionalContext"], str)


def test_hook_graceful_on_missing_db(tmp_path):
    from memor.embed.fake import FakeEmbedder
    from memor.hook_server import handle_request

    e = FakeEmbedder(dim=16)
    req = {"prompt": "test", "cwd": str(tmp_path), "session_id": "s1"}
    result = handle_request(req, db_path=str(tmp_path / "nope.db"), embedder=e)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "Memor:" in ctx


def test_hook_no_embedder_status(tmp_path):
    from memor.hook_server import handle_request
    req = {"prompt": "test", "cwd": str(tmp_path), "session_id": "s1"}
    result = handle_request(req, db_path=str(tmp_path / "nope.db"), embedder=None)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "inactive" in ctx.lower() or "OPENAI_API_KEY" in ctx
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_hook.py -v`
Expected: PASS (these test `handle_request` which already exists from Task 6). If they pass, good — these are integration tests verifying the JSON contract.

- [ ] **Step 3: Create the hook client script**

Create `bin/memor-hook.py`:

```python
#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook — thin client for Memor recall.

Tries to connect to the warm sidecar at ~/.memor/hook.sock.
Falls back to inline execution if sidecar is unavailable.
"""
from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

SOCK_PATH = Path.home() / ".memor" / "hook.sock"
PID_PATH = Path.home() / ".memor" / "hook.pid"


def _send_to_sidecar(request: dict) -> dict | None:
    if not SOCK_PATH.exists():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(str(SOCK_PATH))
        sock.sendall(json.dumps(request).encode())
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
        sock.close()
        return json.loads(b"".join(chunks))
    except (ConnectionRefusedError, FileNotFoundError, TimeoutError, OSError):
        return None


def _start_sidecar() -> bool:
    hook_server = Path(__file__).resolve().parent.parent / "memor" / "hook_server.py"
    if not hook_server.exists():
        return False
    python = sys.executable
    subprocess.Popen(
        [python, str(hook_server)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(30):
        time.sleep(0.1)
        if SOCK_PATH.exists():
            return True
    return False


def _inline_fallback(request: dict) -> dict:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from memor.hook_server import handle_request
        return handle_request(request)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "",
            }
        }


def main():
    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(1)

    result = _send_to_sidecar(request)
    if result is None:
        if _start_sidecar():
            result = _send_to_sidecar(request)
    if result is None:
        result = _inline_fallback(request)

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 4: Make script executable**

```bash
chmod +x bin/memor-hook.py
```

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add bin/memor-hook.py tests/test_hook.py
git commit -m "feat: add Claude Code hook client with sidecar + inline fallback"
```

---

### Task 8: Install-Hook CLI Command

**Files:**
- Modify: `memor/cli.py`
- Create: `tests/test_install_hook.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_install_hook.py`:

```python
import json
from pathlib import Path
from memor.cli import _install_hook_logic


def test_install_hook_creates_settings(tmp_path):
    settings_path = tmp_path / "settings.json"
    hook_path = "/path/to/memor-hook.py"
    _install_hook_logic(settings_path, hook_path)
    data = json.loads(settings_path.read_text())
    hooks = data["hooks"]["UserPromptSubmit"]
    assert len(hooks) == 1
    assert hooks[0]["command"] == f"python3 {hook_path}"
    assert hooks[0]["timeout"] == 5000


def test_install_hook_preserves_existing(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "model": "opus",
        "hooks": {
            "UserPromptSubmit": [
                {"type": "command", "command": "my-other-hook.sh", "timeout": 1000}
            ]
        }
    }))
    hook_path = "/path/to/memor-hook.py"
    _install_hook_logic(settings_path, hook_path)
    data = json.loads(settings_path.read_text())
    assert data["model"] == "opus"
    hooks = data["hooks"]["UserPromptSubmit"]
    assert len(hooks) == 2
    assert hooks[0]["command"] == "my-other-hook.sh"
    assert "memor-hook" in hooks[1]["command"]


def test_install_hook_idempotent(tmp_path):
    settings_path = tmp_path / "settings.json"
    hook_path = "/path/to/memor-hook.py"
    _install_hook_logic(settings_path, hook_path)
    _install_hook_logic(settings_path, hook_path)
    data = json.loads(settings_path.read_text())
    hooks = data["hooks"]["UserPromptSubmit"]
    memor_hooks = [h for h in hooks if "memor-hook" in h["command"]]
    assert len(memor_hooks) == 1


def test_install_hook_updates_existing_memor_entry(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "hooks": {
            "UserPromptSubmit": [
                {"type": "command", "command": "python3 /old/memor-hook.py", "timeout": 1000}
            ]
        }
    }))
    hook_path = "/new/memor-hook.py"
    _install_hook_logic(settings_path, hook_path)
    data = json.loads(settings_path.read_text())
    hooks = data["hooks"]["UserPromptSubmit"]
    assert len(hooks) == 1
    assert "/new/memor-hook.py" in hooks[0]["command"]
    assert hooks[0]["timeout"] == 5000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_install_hook.py -v`
Expected: FAIL — `_install_hook_logic` doesn't exist

- [ ] **Step 3: Implement install-hook**

Add to `memor/cli.py` (before `if __name__ == "__main__":`):

```python
def _install_hook_logic(settings_path: Path, hook_path: str) -> None:
    """Core logic for install-hook, separated for testing."""
    if settings_path.exists():
        data = json.loads(settings_path.read_text())
    else:
        data = {}
    hooks = data.setdefault("hooks", {})
    prompt_hooks = hooks.setdefault("UserPromptSubmit", [])
    entry = {"type": "command", "command": f"python3 {hook_path}", "timeout": 5000}
    existing_idx = None
    for i, h in enumerate(prompt_hooks):
        if "memor-hook" in h.get("command", ""):
            existing_idx = i
            break
    if existing_idx is not None:
        prompt_hooks[existing_idx] = entry
    else:
        prompt_hooks.append(entry)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2))


@app.command("install-hook")
def install_hook():
    """Install the Claude Code recall hook into ~/.claude/settings.json."""
    hook_path = str(Path(__file__).resolve().parent.parent / "bin" / "memor-hook.py")
    settings_path = Path.home() / ".claude" / "settings.json"
    _install_hook_logic(settings_path, hook_path)
    typer.echo(f"Hook installed: {hook_path}")
    typer.echo(f"Settings updated: {settings_path}")
    typer.echo()
    typer.echo("Next steps:")
    typer.echo("  1. Start the daemon: memor daemon")
    typer.echo("     (First run ingests existing sessions — takes ~2-5 minutes)")
    typer.echo("  2. Extractive distillation runs automatically (no API key needed)")
    typer.echo("  3. For richer memories, set ANTHROPIC_API_KEY")
    typer.echo("  4. Open the dashboard: memor dashboard")
```

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add memor/cli.py tests/test_install_hook.py
git commit -m "feat: add install-hook CLI command with cold-start guidance"
```

---

### Task 9: FastAPI Dashboard Server

**Files:**
- Create: `memor/dashboard/__init__.py`
- Create: `memor/dashboard/server.py`
- Create: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_dashboard.py`:

```python
import json
from pathlib import Path
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


def _make_app(tmp_path):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(id="a1", kind="memory", project="myproj", source="distill",
                   text="use argon2 for hashing", token_count=6,
                   created_at=100.0, meta={"mem_type": "decision", "session_id": "s1"})
    s.add_artifacts([art], e.embed([art.text]))
    s.log_recall("myproj", "password hashing", 2, 0.85, 120, 45.0, "ok", "sess1")
    s.log_recall("myproj", "auth bug", 0, 0.0, 0, 12.0, "no_hits", "sess2")

    from memor.dashboard.server import create_app
    app = create_app(db_path)
    return app


def test_summary_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["total_recalls"] == 2
    assert data["total_tokens"] == 120
    assert data["hit_rate"] == 0.5


def test_projects_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/projects")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["project"] == "myproj"


def test_recalls_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/recalls?limit=10")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2


def test_recalls_filter_by_project(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/recalls?project=nonexistent")
    assert r.status_code == 200
    assert len(r.json()) == 0


def test_health_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert "onboarding_status" in data
    assert "artifact_counts" in data


def test_savings_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/savings")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_empty_db(tmp_path):
    from fastapi.testclient import TestClient
    db_path = str(tmp_path / "empty.db")
    SqliteStore(db_path, dim=16)
    from memor.dashboard.server import create_app
    app = create_app(db_path)
    client = TestClient(app)
    r = client.get("/api/summary")
    assert r.status_code == 200
    assert r.json()["total_recalls"] == 0


def test_index_html_served(tmp_path):
    from fastapi.testclient import TestClient
    db_path = str(tmp_path / "m.db")
    SqliteStore(db_path, dim=16)
    from memor.dashboard.server import create_app
    app = create_app(db_path)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard.py -v`
Expected: FAIL — `memor.dashboard.server` doesn't exist

- [ ] **Step 3: Create dashboard package**

Create `memor/dashboard/__init__.py` (empty file):

```python
```

- [ ] **Step 4: Implement FastAPI server**

Create `memor/dashboard/server.py`:

```python
from __future__ import annotations
import os
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from memor.store.sqlite_store import SqliteStore

STATIC_DIR = Path(__file__).parent / "static"


def create_app(db_path: str | None = None) -> FastAPI:
    if db_path is None:
        db_path = str(Path.home() / ".memor" / "memor.db")

    app = FastAPI(title="Memor Dashboard")
    _db_path = db_path

    def _store() -> SqliteStore:
        return SqliteStore(_db_path, dim=_get_dim(_db_path))

    @app.get("/", response_class=HTMLResponse)
    def index():
        html_path = STATIC_DIR / "index.html"
        if html_path.exists():
            return HTMLResponse(html_path.read_text())
        return HTMLResponse("<h1>Memor Dashboard</h1><p>index.html not found</p>")

    @app.get("/api/summary")
    def summary():
        store = _store()
        return store.get_recall_stats()

    @app.get("/api/projects")
    def projects():
        store = _store()
        return store.get_project_stats()

    @app.get("/api/recalls")
    def recalls(limit: int = Query(50, ge=1, le=500),
                project: str | None = Query(None)):
        store = _store()
        return store.get_recent_recalls(limit=limit, project=project)

    @app.get("/api/savings")
    def savings():
        store = _store()
        rows = store.db.execute("""
            SELECT project,
                   SUM(tokens_injected) as recalled_tokens,
                   AVG(CASE WHEN hits_count > 0 THEN top_score END) as avg_relevance
            FROM recall_log
            WHERE status IN ('ok', 'extractive_only')
            GROUP BY project
        """).fetchall()
        result = []
        for r in rows:
            project = r["project"]
            full_ctx = store.db.execute(
                "SELECT SUM(token_count) as total FROM artifacts WHERE project=? AND active=1",
                (project,)).fetchone()["total"] or 0
            recalled = r["recalled_tokens"] or 0
            result.append({
                "project": project,
                "recalled_tokens": recalled,
                "full_context_tokens": full_ctx,
                "reduction_pct": round((1 - recalled / full_ctx) * 100, 1) if full_ctx > 0 else 0,
                "avg_relevance": round(r["avg_relevance"] or 0, 3),
            })
        return result

    @app.get("/api/health")
    def health():
        store = _store()
        db_size = os.path.getsize(_db_path) if os.path.exists(_db_path) else 0
        counts = {}
        for row in store.db.execute(
            "SELECT kind, COUNT(*) as c FROM artifacts WHERE active=1 GROUP BY kind"
        ).fetchall():
            counts[row["kind"]] = row["c"]
        last_ingest = store.db.execute(
            "SELECT MAX(created_at) as t FROM artifacts"
        ).fetchone()["t"]
        dim_row = store.db.execute("SELECT value FROM meta WHERE key='dim'").fetchone()
        return {
            "onboarding_status": store.get_onboarding_status(),
            "db_size_bytes": db_size,
            "artifact_counts": counts,
            "last_ingest_timestamp": last_ingest,
            "embedder_dim": int(dim_row["value"]) if dim_row else None,
        }

    return app


def _get_dim(db_path: str) -> int:
    """Read dim from meta table, default to 1536 (OpenAI)."""
    import sqlite3
    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT value FROM meta WHERE key='dim'").fetchone()
        db.close()
        if row:
            return int(row["value"])
    except Exception:
        pass
    return 1536
```

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add memor/dashboard/__init__.py memor/dashboard/server.py tests/test_dashboard.py
git commit -m "feat: add FastAPI dashboard with summary, projects, recalls, savings, health APIs"
```

---

### Task 10: Dashboard HTML

**Files:**
- Create: `memor/dashboard/static/index.html`

- [ ] **Step 1: Create static directory**

```bash
mkdir -p memor/dashboard/static
```

- [ ] **Step 2: Create self-contained dashboard HTML**

Create `memor/dashboard/static/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Memor Dashboard</title>
<style>
:root {
  --bg: #0f172a; --surface: #1e293b; --border: #334155;
  --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
  --green: #4ade80; --yellow: #fbbf24; --gray: #64748b;
  --red: #f87171;
}
[data-theme="light"] {
  --bg: #f8fafc; --surface: #ffffff; --border: #e2e8f0;
  --text: #1e293b; --muted: #64748b; --accent: #0284c7;
  --green: #16a34a; --yellow: #ca8a04; --gray: #94a3b8;
  --red: #dc2626;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.5; }
.container { max-width: 1200px; margin: 0 auto; padding: 24px; }
header { display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 32px; }
header h1 { font-size: 24px; font-weight: 600; }
.theme-toggle { background: var(--surface); border: 1px solid var(--border);
  color: var(--text); padding: 6px 12px; border-radius: 6px; cursor: pointer; }
.banner { background: var(--surface); border: 1px solid var(--accent);
  border-radius: 8px; padding: 16px; margin-bottom: 24px; }
.banner h3 { color: var(--accent); margin-bottom: 8px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px; margin-bottom: 32px; }
.card { background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 16px; }
.card .value { font-size: 28px; font-weight: 700; color: var(--accent); }
.card .label { font-size: 13px; color: var(--muted); margin-top: 4px; }
section { margin-bottom: 32px; }
section h2 { font-size: 18px; margin-bottom: 12px; }
.note { font-size: 13px; color: var(--muted); margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; background: var(--surface);
  border-radius: 8px; overflow: hidden; }
th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); }
th { font-size: 12px; text-transform: uppercase; color: var(--muted);
  cursor: pointer; user-select: none; }
th:hover { color: var(--accent); }
td { font-size: 14px; }
.badge { padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 500; }
.badge-ok { background: var(--green); color: #000; }
.badge-extractive_only { background: var(--yellow); color: #000; }
.badge-no_hits { background: var(--gray); color: #fff; }
.badge-empty_db { background: var(--red); color: #fff; }
.savings-bar { display: flex; align-items: center; gap: 12px; margin: 8px 0; }
.savings-bar .bar-bg { flex: 1; height: 24px; background: var(--border);
  border-radius: 4px; overflow: hidden; position: relative; }
.savings-bar .bar-fill { height: 100%; background: var(--accent);
  border-radius: 4px; transition: width 0.3s; }
.savings-bar .bar-label { font-size: 13px; min-width: 60px; text-align: right; }
.project-row { cursor: pointer; }
.project-row:hover { background: var(--bg); }
.filter-info { font-size: 13px; color: var(--accent); margin-bottom: 8px; }
.empty { text-align: center; padding: 40px; color: var(--muted); }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>Memor Dashboard</h1>
    <button class="theme-toggle" onclick="toggleTheme()">Toggle Theme</button>
  </header>
  <div id="banner"></div>
  <div id="hero" class="cards"></div>
  <section>
    <h2>Recall Quality &amp; Efficiency</h2>
    <p class="note">Token savings estimates how much context Memor selectively recalled vs. sending everything. Relevance score measures how well the recalled context matched your query. Both matter.</p>
    <div id="savings"></div>
  </section>
  <section>
    <h2>Project Breakdown</h2>
    <div id="filter-info" class="filter-info"></div>
    <table id="projects-table">
      <thead><tr>
        <th data-sort="project">Project</th>
        <th data-sort="recalls">Recalls</th>
        <th data-sort="tokens">Tokens</th>
        <th data-sort="avg_score">Avg Score</th>
        <th data-sort="ok_count">OK</th>
        <th data-sort="no_hits_count">No Hits</th>
        <th data-sort="extractive_count">Extractive</th>
      </tr></thead>
      <tbody id="projects-body"></tbody>
    </table>
  </section>
  <section>
    <h2>Recent Recalls</h2>
    <table>
      <thead><tr>
        <th>Time</th><th>Project</th><th>Query</th><th>Hits</th>
        <th>Score</th><th>Tokens</th><th>Latency</th><th>Status</th>
      </tr></thead>
      <tbody id="recalls-body"></tbody>
    </table>
  </section>
</div>
<script>
let currentProject = null;
let projectsData = [];

function toggleTheme() {
  const t = document.documentElement.getAttribute('data-theme') === 'light' ? '' : 'light';
  document.documentElement.setAttribute('data-theme', t);
  localStorage.setItem('theme', t);
}
if (localStorage.getItem('theme') === 'light') document.documentElement.setAttribute('data-theme', 'light');

async function fetchJSON(url) { return (await fetch(url)).json(); }

function ts(epoch) {
  if (!epoch) return '-';
  const d = new Date(epoch * 1000);
  return d.toLocaleString();
}

async function loadBanner() {
  const h = await fetchJSON('/api/health');
  const el = document.getElementById('banner');
  const msgs = {
    no_data: 'No data yet. Run <code>memor daemon</code> to start ingesting sessions.',
    ingesting: 'Sessions ingested but not yet distilled. Distillation runs on next daemon poll cycle.',
    extractive: 'Using extractive distillation. Set <code>ANTHROPIC_API_KEY</code> for richer LLM-powered memories.',
    full: null
  };
  const msg = msgs[h.onboarding_status];
  if (msg) {
    el.innerHTML = '<div class="banner"><h3>Getting Started</h3><p>' + msg + '</p></div>';
  } else {
    el.innerHTML = '';
  }
}

async function loadHero() {
  const s = await fetchJSON('/api/summary');
  const el = document.getElementById('hero');
  const items = [
    { v: s.total_recalls, l: 'Total Recalls' },
    { v: (s.total_tokens || 0).toLocaleString(), l: 'Tokens Injected' },
    { v: ((s.hit_rate || 0) * 100).toFixed(1) + '%', l: 'Hit Rate' },
    { v: (s.avg_latency_ms || 0).toFixed(0) + 'ms', l: 'Avg Latency' },
  ];
  el.innerHTML = items.map(i =>
    '<div class="card"><div class="value">' + i.v + '</div><div class="label">' + i.l + '</div></div>'
  ).join('');
}

async function loadSavings() {
  const data = await fetchJSON('/api/savings');
  const el = document.getElementById('savings');
  if (!data.length) { el.innerHTML = '<p class="empty">No recall data yet</p>'; return; }
  el.innerHTML = data.map(d =>
    '<div class="savings-bar">' +
    '<span style="min-width:120px">' + d.project + '</span>' +
    '<div class="bar-bg"><div class="bar-fill" style="width:' + Math.min(d.reduction_pct, 100) + '%"></div></div>' +
    '<span class="bar-label">' + d.reduction_pct + '% saved</span>' +
    '<span class="bar-label">relevance: ' + d.avg_relevance.toFixed(2) + '</span>' +
    '</div>'
  ).join('');
}

async function loadProjects() {
  projectsData = await fetchJSON('/api/projects');
  renderProjects();
}

function renderProjects() {
  const el = document.getElementById('projects-body');
  if (!projectsData.length) { el.innerHTML = '<tr><td colspan="7" class="empty">No data</td></tr>'; return; }
  el.innerHTML = projectsData.map(p =>
    '<tr class="project-row" onclick="filterProject(\'' + p.project + '\')">' +
    '<td>' + p.project + '</td>' +
    '<td>' + p.recalls + '</td>' +
    '<td>' + (p.tokens || 0) + '</td>' +
    '<td>' + (p.avg_score ? p.avg_score.toFixed(3) : '-') + '</td>' +
    '<td>' + (p.ok_count || 0) + '</td>' +
    '<td>' + (p.no_hits_count || 0) + '</td>' +
    '<td>' + (p.extractive_count || 0) + '</td>' +
    '</tr>'
  ).join('');
}

function filterProject(p) {
  currentProject = currentProject === p ? null : p;
  document.getElementById('filter-info').textContent =
    currentProject ? 'Filtered: ' + currentProject + ' (click again to clear)' : '';
  loadRecalls();
}

async function loadRecalls() {
  let url = '/api/recalls?limit=50';
  if (currentProject) url += '&project=' + encodeURIComponent(currentProject);
  const data = await fetchJSON(url);
  const el = document.getElementById('recalls-body');
  if (!data.length) { el.innerHTML = '<tr><td colspan="8" class="empty">No recalls yet</td></tr>'; return; }
  el.innerHTML = data.map(r =>
    '<tr>' +
    '<td>' + ts(r.timestamp) + '</td>' +
    '<td>' + (r.project || '') + '</td>' +
    '<td>' + (r.query_preview || '') + '</td>' +
    '<td>' + (r.hits_count || 0) + '</td>' +
    '<td>' + (r.top_score ? r.top_score.toFixed(3) : '-') + '</td>' +
    '<td>' + (r.tokens_injected || 0) + '</td>' +
    '<td>' + (r.latency_ms ? r.latency_ms.toFixed(0) + 'ms' : '-') + '</td>' +
    '<td><span class="badge badge-' + (r.status || 'ok') + '">' + (r.status || 'ok') + '</span></td>' +
    '</tr>'
  ).join('');
}

document.querySelectorAll('#projects-table th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const key = th.dataset.sort;
    projectsData.sort((a, b) => {
      if (typeof a[key] === 'string') return a[key].localeCompare(b[key]);
      return (b[key] || 0) - (a[key] || 0);
    });
    renderProjects();
  });
});

async function refresh() {
  await Promise.all([loadBanner(), loadHero(), loadSavings(), loadProjects(), loadRecalls()]);
}
refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>
```

- [ ] **Step 3: Run dashboard test to verify HTML is served**

Run: `.venv/bin/python -m pytest tests/test_dashboard.py::test_index_html_served -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add memor/dashboard/static/index.html
git commit -m "feat: add self-contained dashboard HTML with inline CSS"
```

---

### Task 11: Dashboard CLI Command

**Files:**
- Modify: `memor/cli.py`

- [ ] **Step 1: Add dashboard command**

Add to `memor/cli.py` (before `if __name__ == "__main__":`):

```python
@app.command("dashboard")
def dashboard(port: int = typer.Option(8420, help="Port to serve on"),
              no_open: bool = typer.Option(False, help="Don't open browser"),
              db: str = typer.Option(str(Path.home() / ".memor" / "memor.db"))):
    """Launch the web dashboard."""
    import uvicorn
    from memor.dashboard.server import create_app
    db_resolved = _db_path(db)
    if not Path(db_resolved).exists():
        typer.echo(f"Database not found at {db_resolved}")
        typer.echo("Run 'memor daemon' first to create and populate the database.")
        raise typer.Exit(1)
    app_instance = create_app(db_resolved)
    if not no_open:
        import webbrowser
        import threading
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    typer.echo(f"Memor dashboard: http://localhost:{port}")
    uvicorn.run(app_instance, host="127.0.0.1", port=port, log_level="warning")
```

- [ ] **Step 2: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add memor/cli.py
git commit -m "feat: add dashboard CLI command — serves FastAPI on localhost:8420"
```

---

### Task 12: Update skill/recall.py to Use Shared Core

**Files:**
- Modify: `skill/recall.py`

- [ ] **Step 1: Rewrite skill/recall.py to delegate to shared core**

Replace `skill/recall.py`:

```python
"""Memor recall skill -- retrieves relevant context for an agent."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_DB = str(Path.home() / ".memor" / "memor.db")


def main():
    p = argparse.ArgumentParser(description="Recall relevant context from Memor")
    p.add_argument("--query", required=True, help="Question or task to recall context for")
    p.add_argument("--project", required=True, help="Project name to scope retrieval")
    p.add_argument("--db", default=DEFAULT_DB, help=f"Path to memory DB (default: {DEFAULT_DB})")
    p.add_argument("--k", type=int, default=8, help="Number of hits (default: 8)")
    p.add_argument("--fake", action="store_true", help="Use fake embedder (testing only)")
    a = p.parse_args()

    db_path = Path(a.db)
    if not db_path.exists():
        print(f"ERROR: Database not found at {a.db}")
        print()
        print("The memor memory database has not been created yet.")
        print("Run the auto-ingest daemon to populate it:")
        print()
        print("  memor daemon")
        print()
        print("This will watch ~/.claude/projects/ and ingest session transcripts.")
        sys.exit(1)

    if a.fake:
        from memor.embed.fake import FakeEmbedder
        embedder = FakeEmbedder(dim=16)
    else:
        import os
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            from memor.embed.api import APIEmbedder
            base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            embedder = APIEmbedder(base_url=base_url, api_key=api_key)
        else:
            try:
                from memor.embed.local import LocalEmbedder
                embedder = LocalEmbedder()
            except ImportError:
                print("ERROR: No embedder available.")
                print("Set OPENAI_API_KEY or pip install memor-ai[local]")
                sys.exit(1)

    from memor.recall import recall
    result = recall(a.query, a.project, a.db, embedder=embedder, k=a.k, threshold=0.3)
    print(result.formatted_context)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run existing skill tests**

Run: `.venv/bin/python -m pytest tests/test_skill_recall.py -v`
Expected: PASS (the test should still work since the output format is similar)

- [ ] **Step 3: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add skill/recall.py
git commit -m "refactor: delegate skill/recall.py to shared memor.recall core"
```

---

### Task 13: Add Daemon Progress Counter

**Files:**
- Modify: `memor/daemon.py:146-186` (`run_poll_cycle`)

- [ ] **Step 1: Add progress reporting to run_poll_cycle**

In `memor/daemon.py`, update `run_poll_cycle` to report progress during bulk ingestion:

```python
def run_poll_cycle(
    state: dict[str, float],
    store: SqliteStore,
    embedder,
    projects_dir: Path = CLAUDE_PROJECTS_DIR,
    llm=None,
    distilled: set[str] | None = None,
) -> tuple[dict[str, float], set[str]]:
    """Run one poll cycle: ingest new files, then distill new sessions.
    Returns (updated ingest state, updated distilled set)."""
    if distilled is None:
        distilled = set()

    new_ingested = False
    transcripts = scan_transcripts(projects_dir)
    pending = [(p, proj) for p, proj in transcripts
               if state.get(str(p)) is None or p.stat().st_mtime > state.get(str(p), 0)]
    total_pending = len(pending)

    for idx, (path, project) in enumerate(pending):
        path_str = str(path)
        try:
            count = ingest_file(path, project, store, embedder)
            state[path_str] = path.stat().st_mtime
            if count > 0:
                if total_pending > 10:
                    print(f"  [{idx+1}/{total_pending}] ingested {count} chunks from {path.name} (project: {project})")
                else:
                    print(f"  ingested {count} chunks from {path.name} (project: {project})")
                new_ingested = True
            else:
                if total_pending <= 10:
                    print(f"  skipped {path.name} (0 chunks after filtering)")
        except Exception as e:
            print(f"  ERROR ingesting {path.name}: {e}")

    # Auto-distill new sessions (LLM if available, extractive fallback otherwise)
    if new_ingested:
        mode = "abstractive" if llm else "extractive (LLM-free)"
        print(f"  running {mode} distillation on new sessions...")
        distilled = distill_new_sessions(store, embedder, llm, distilled)

    return state, distilled
```

- [ ] **Step 2: Run daemon tests**

Run: `.venv/bin/python -m pytest tests/test_daemon.py -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add memor/daemon.py
git commit -m "feat: add progress counter to daemon for bulk ingestion"
```

---

## Self-Review

**1. Spec coverage check:**
- [x] Section 1 (Noise filter: whitelist) → Task 3
- [x] Section 2.1 (Project resolver) → Task 4
- [x] Section 2.2 (Hook sidecar) → Task 6
- [x] Section 2 (Hook client) → Task 7
- [x] Section 2 (Recall log) → Task 2 (store methods)
- [x] Section 2 (Shared recall core) → Task 5
- [x] Section 2 (Install-hook) → Task 8
- [x] Section 2 (Status messages) → Task 5 (in recall.py)
- [x] Section 3 (Dashboard server) → Task 9
- [x] Section 3 (Dashboard HTML) → Task 10
- [x] Section 3 (Dashboard command) → Task 11
- [x] Section 4 (Remove TUI) → Task 1
- [x] Section 5.1 (WAL mode) → Task 2
- [x] Section 5.2 (Dimension safety) → Task 2
- [x] Section 5.3 (Cold-start) → Task 8 (install-hook message), Task 9 (onboarding_status), Task 13 (progress)
- [x] Update skill/recall.py → Task 12

**2. Placeholder scan:** No TBD, TODO, or placeholder patterns found.

**3. Type consistency check:**
- `RecallResult` used consistently in Task 5 (definition) and Tasks 6, 7, 12 (consumers)
- `handle_request` signature matches between Task 6 (definition) and Task 7 (import)
- `create_app(db_path)` matches between Task 9 (definition) and Task 11 (usage)
- `_install_hook_logic(settings_path, hook_path)` matches between Task 8 (definition) and tests
- `log_recall()` params match between Task 2 (definition) and Task 6 (caller)
- `resolve_project(cwd)` matches between Task 4 (definition) and Task 6 (caller)
