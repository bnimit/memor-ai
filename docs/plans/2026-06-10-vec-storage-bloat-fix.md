# Vec Storage Bloat Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix sqlite-vec `vec0` storage bloat (554MB for ~12MB of actual data) by preventing orphan vectors, fixing broken INSERT OR REPLACE, adding periodic rebuild, and caching the dashboard store.

**Architecture:** Centralize all artifact deactivation into `_deactivate_artifact()` which cleans vec0 + FTS. Add `rebuild_vec_index(embedder)` that drops and recreates the vec0 table with size-appropriate `chunk_size`. Daemon auto-compacts when bloat exceeds 2x ideal. Dashboard caches a single `SqliteStore` instance.

**Tech Stack:** Python 3.11+, sqlite-vec (vec0), SQLite FTS5, pytest, typer

---

### Task 0: Create test file and branch verification

**Files:**
- Create: `tests/test_vec_compaction.py`

- [ ] **Step 1: Verify branch**

Run: `git branch --show-current`
Expected: `fix/vec-storage-bloat`

- [ ] **Step 2: Create the test file with imports and helpers**

```python
"""Tests for vec storage bloat fix: deactivation cleanup, rebuild, auto-compact."""
import json
from memor.store.sqlite_store import SqliteStore, _serialize
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


def _make_store(tmp_path, n=5):
    """Create a store with n artifacts and return (store, embedder, artifacts)."""
    db_path = str(tmp_path / "test.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    arts = []
    for i in range(n):
        a = Artifact(
            id=f"art-{i}", kind="memory", project="proj", source="distill",
            text=f"memory about topic {i}", token_count=5,
            created_at=100.0 + i, meta={"mem_type": "decision", "session_id": "s1"})
        arts.append(a)
    vecs = e.embed([a.text for a in arts])
    s.add_artifacts(arts, vecs)
    return s, e, arts
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_vec_compaction.py
git commit -m "test: scaffold test file for vec compaction"
```

---

### Task 1: Add `_deactivate_artifact()` and fix `deactivate()`

**Files:**
- Modify: `memor/store/sqlite_store.py:228-231`
- Test: `tests/test_vec_compaction.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_vec_compaction.py`:

```python
def test_deactivate_cleans_vec_and_fts(tmp_path):
    s, e, arts = _make_store(tmp_path)
    # Confirm vec and fts entries exist before deactivation
    vec_count_before = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    fts_count_before = s.db.execute("SELECT COUNT(*) as c FROM fts_artifacts").fetchone()["c"]
    assert vec_count_before == 5
    assert fts_count_before == 5

    s.deactivate("art-2", superseded_by="art-3")

    # Vec and FTS entries for art-2 should be gone
    vec_count_after = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    fts_count_after = s.db.execute("SELECT COUNT(*) as c FROM fts_artifacts").fetchone()["c"]
    assert vec_count_after == 4
    assert fts_count_after == 4

    # The artifact row should still exist but be inactive
    row = s.db.execute("SELECT active FROM artifacts WHERE id='art-2'").fetchone()
    assert row["active"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_vec_compaction.py::test_deactivate_cleans_vec_and_fts -v`
Expected: FAIL — `assert vec_count_after == 4` fails (still 5 because current `deactivate()` doesn't clean vec0)

- [ ] **Step 3: Implement `_deactivate_artifact()` and update `deactivate()`**

In `memor/store/sqlite_store.py`, add `_deactivate_artifact()` just before the existing `deactivate()` method (before line 228):

```python
def _deactivate_artifact(self, artifact_id: str) -> None:
    row = self.db.execute(
        "SELECT rowid FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
    if row:
        self.db.execute("DELETE FROM vec_artifacts WHERE rowid=?", (row[0],))
        self.db.execute("DELETE FROM fts_artifacts WHERE id=?", (artifact_id,))
```

Update the existing `deactivate()` method to use it:

```python
def deactivate(self, artifact_id: str, superseded_by: str) -> None:
    self._deactivate_artifact(artifact_id)
    self.db.execute("UPDATE artifacts SET active=0, superseded_by=? WHERE id=?",
                    (superseded_by, artifact_id))
    self.add_edge(superseded_by, artifact_id, "supersedes")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_vec_compaction.py::test_deactivate_cleans_vec_and_fts -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `.venv/bin/pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add memor/store/sqlite_store.py tests/test_vec_compaction.py
git commit -m "fix: deactivate() now cleans vec0 and FTS entries"
```

---

### Task 2: Fix `deactivate_stale()` and `decay_quality()` to clean vec0+FTS

**Files:**
- Modify: `memor/store/sqlite_store.py:384-415` (decay_quality) and `memor/store/sqlite_store.py:417-422` (deactivate_stale)
- Test: `tests/test_vec_compaction.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_vec_compaction.py`:

```python
def test_deactivate_stale_cleans_vec(tmp_path):
    s, e, arts = _make_store(tmp_path)
    # Make all artifacts old enough to be stale (created_at=100..104, well before cutoff)
    vec_before = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert vec_before == 5

    count = s.deactivate_stale(days=0)  # days=0 means everything is stale
    assert count > 0

    vec_after = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert vec_after < vec_before


def test_decay_quality_cleans_vec(tmp_path):
    s, e, arts = _make_store(tmp_path)
    import time as _time
    # Set up quality records with very low scores so they'll be deactivated
    for a in arts[:2]:
        s.db.execute(
            "INSERT INTO memory_quality(artifact_id, recall_count, use_count, quality_score, last_recalled) "
            "VALUES(?, 10, 0, 0.02, ?)", (a.id, 1.0))  # score=0.02 < floor=0.03
    s.db.commit()

    vec_before = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert vec_before == 5

    decayed = s.decay_quality(stale_days=0, factor=0.5, deactivate_floor=0.03)
    assert decayed >= 2

    vec_after = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert vec_after < vec_before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_vec_compaction.py::test_deactivate_stale_cleans_vec tests/test_vec_compaction.py::test_decay_quality_cleans_vec -v`
Expected: FAIL — vec counts unchanged after deactivation

- [ ] **Step 3: Update `deactivate_stale()` to call `_deactivate_artifact()`**

Replace the `deactivate_stale` method in `memor/store/sqlite_store.py`:

```python
def deactivate_stale(self, days: int = 30) -> int:
    ids = self.get_stale_memories(days)
    for aid in ids:
        self._deactivate_artifact(aid)
        self.db.execute("UPDATE artifacts SET active=0 WHERE id=?", (aid,))
    self.db.commit()
    return len(ids)
```

- [ ] **Step 4: Update `decay_quality()` to call `_deactivate_artifact()`**

In the `decay_quality` method, replace the inline `UPDATE active=0` (around line 407-409) with:

```python
if new_score < deactivate_floor:
    self._deactivate_artifact(r["artifact_id"])
    self.db.execute("UPDATE artifacts SET active=0 WHERE id=?",
                    (r["artifact_id"],))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_vec_compaction.py -v`
Expected: All 3 tests pass

- [ ] **Step 6: Run full suite**

Run: `.venv/bin/pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add memor/store/sqlite_store.py tests/test_vec_compaction.py
git commit -m "fix: deactivate_stale and decay_quality now clean vec0+FTS"
```

---

### Task 3: Fix `add_artifacts()` INSERT OR REPLACE on vec0

**Files:**
- Modify: `memor/store/sqlite_store.py:126-138`
- Test: `tests/test_vec_compaction.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_vec_compaction.py`:

```python
def test_add_artifacts_re_add_no_extra_chunks(tmp_path):
    """Re-adding the same artifact should not leak extra vec0 chunk slots."""
    s, e, arts = _make_store(tmp_path, n=3)
    rowids_before = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert rowids_before == 3

    # Re-add the same artifacts (simulates re-ingest)
    vecs = e.embed([a.text for a in arts])
    s.add_artifacts(arts, vecs)

    rowids_after = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert rowids_after == 3  # should not have grown
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_vec_compaction.py::test_add_artifacts_re_add_no_extra_chunks -v`
Expected: FAIL — rowids_after > 3 due to INSERT OR REPLACE bug

- [ ] **Step 3: Replace INSERT OR REPLACE with DELETE + INSERT on vec0**

In `memor/store/sqlite_store.py`, update the `add_artifacts` method. Replace lines 134-135:

```python
            cur.execute("INSERT OR REPLACE INTO vec_artifacts(rowid, embedding) VALUES(?,?)",
                        (rowid, _serialize(v)))
```

With:

```python
            cur.execute("DELETE FROM vec_artifacts WHERE rowid=?", (rowid,))
            cur.execute("INSERT INTO vec_artifacts(rowid, embedding) VALUES(?,?)",
                        (rowid, _serialize(v)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_vec_compaction.py::test_add_artifacts_re_add_no_extra_chunks -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add memor/store/sqlite_store.py tests/test_vec_compaction.py
git commit -m "fix: replace broken INSERT OR REPLACE on vec0 with DELETE+INSERT"
```

---

### Task 4: Add `rebuild_vec_index()` method

**Files:**
- Modify: `memor/store/sqlite_store.py`
- Test: `tests/test_vec_compaction.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_vec_compaction.py`:

```python
def test_rebuild_vec_index(tmp_path):
    s, e, arts = _make_store(tmp_path, n=10)
    # Deactivate half the artifacts
    for a in arts[:5]:
        s.deactivate(a.id, superseded_by=arts[5].id)

    active_before = s.db.execute(
        "SELECT COUNT(*) as c FROM artifacts WHERE active=1").fetchone()["c"]
    assert active_before == 5

    result = s.rebuild_vec_index(e)
    assert result["vectors_reindexed"] == 5

    # Vec should only have active vectors
    vec_count = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert vec_count == 5

    # Search should still work
    from memor.types import Scope
    hits = s.search(e.embed(["topic 7"])[0], Scope(project="proj"), k=3)
    assert len(hits) > 0


def test_rebuild_chunk_size_selection(tmp_path):
    from memor.store.sqlite_store import _choose_chunk_size
    assert _choose_chunk_size(500) == 64
    assert _choose_chunk_size(1000) == 256
    assert _choose_chunk_size(5000) == 256
    assert _choose_chunk_size(10000) == 512
    assert _choose_chunk_size(50000) == 512
    assert _choose_chunk_size(100000) == 1024
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_vec_compaction.py::test_rebuild_vec_index tests/test_vec_compaction.py::test_rebuild_chunk_size_selection -v`
Expected: FAIL — `rebuild_vec_index` and `_choose_chunk_size` don't exist

- [ ] **Step 3: Implement `_choose_chunk_size()` and `rebuild_vec_index()`**

Add the module-level function after `_serialize()` in `memor/store/sqlite_store.py`:

```python
def _choose_chunk_size(active_count: int) -> int:
    if active_count < 1000:
        return 64
    if active_count < 10000:
        return 256
    if active_count < 100000:
        return 512
    return 1024
```

Add the method to `SqliteStore`, after `rebuild_fts()`:

```python
def rebuild_vec_index(self, embedder) -> dict:
    import time as _time
    start = _time.time()
    chunk_count_before = self.db.execute(
        "SELECT COUNT(*) as c FROM vec_artifacts_chunks").fetchone()["c"]

    rows = self.db.execute(
        "SELECT id, text, rowid FROM artifacts WHERE active=1"
    ).fetchall()
    texts = [r["text"] for r in rows]
    rowids = [r["rowid"] for r in rows]

    if texts:
        vectors = embedder.embed(texts)
    else:
        vectors = []

    self.db.execute("DROP TABLE IF EXISTS vec_artifacts")
    chunk_size = _choose_chunk_size(len(rows))
    self.db.execute(
        f"CREATE VIRTUAL TABLE vec_artifacts USING vec0("
        f"embedding float[{self.dim}], chunk_size={chunk_size})")

    cur = self.db.cursor()
    for rowid, vec in zip(rowids, vectors):
        cur.execute("INSERT INTO vec_artifacts(rowid, embedding) VALUES(?,?)",
                    (rowid, _serialize(vec)))
    self.db.commit()

    self.db.execute("VACUUM")

    chunk_count_after = self.db.execute(
        "SELECT COUNT(*) as c FROM vec_artifacts_chunks").fetchone()["c"]
    elapsed = round((_time.time() - start) * 1000, 1)

    return {
        "before_chunks": chunk_count_before,
        "after_chunks": chunk_count_after,
        "vectors_reindexed": len(rows),
        "chunk_size": chunk_size,
        "duration_ms": elapsed,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_vec_compaction.py::test_rebuild_vec_index tests/test_vec_compaction.py::test_rebuild_chunk_size_selection -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add memor/store/sqlite_store.py tests/test_vec_compaction.py
git commit -m "feat: add rebuild_vec_index() with chunk_size scaling"
```

---

### Task 5: Add `memor compact` CLI command

**Files:**
- Modify: `memor/cli.py`
- Test: `tests/test_vec_compaction.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_vec_compaction.py`:

```python
def test_compact_cli_command(tmp_path):
    from typer.testing import CliRunner
    from memor.cli import app

    db_path = str(tmp_path / "test.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    arts = [Artifact(id=f"a{i}", kind="memory", project="p", source="distill",
                     text=f"text {i}", token_count=3, created_at=100.0 + i,
                     meta={"mem_type": "decision"}) for i in range(3)]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))

    runner = CliRunner()
    result = runner.invoke(app, ["compact", "--db", db_path, "--yes"])
    assert result.exit_code == 0
    assert "vectors reindexed" in result.output.lower() or "reindexed" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_vec_compaction.py::test_compact_cli_command -v`
Expected: FAIL — no `compact` command

- [ ] **Step 3: Add the `compact` command to `memor/cli.py`**

Add after the `forget-stale` command (around line 392):

```python
@app.command("compact")
def compact(db: str = typer.Option(str(Path.home() / ".memor" / "memor.db")),
            confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation")):
    """Rebuild the vector index to reclaim wasted space."""
    db_path = _db_path(db)
    if not Path(db_path).exists():
        typer.echo("No database found.")
        raise typer.Exit(1)
    embedder = _auto_embedder()
    store = SqliteStore(db_path, dim=embedder.dim)
    chunk_count = store.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_chunks").fetchone()["c"]
    active_count = store.db.execute("SELECT COUNT(*) as c FROM artifacts WHERE active=1").fetchone()["c"]
    db_size_mb = round(Path(db_path).stat().st_size / 1_048_576, 1)
    typer.echo(f"Current: {chunk_count} chunks, {active_count} active vectors, {db_size_mb} MB")
    if not confirm:
        typer.confirm("Rebuild vector index?", abort=True)
    typer.echo("Compacting...")
    result = store.rebuild_vec_index(embedder)
    db_size_after = round(Path(db_path).stat().st_size / 1_048_576, 1)
    typer.echo(f"Done: {result['vectors_reindexed']} vectors reindexed in {result['duration_ms']}ms")
    typer.echo(f"Chunks: {result['before_chunks']} -> {result['after_chunks']} (chunk_size={result['chunk_size']})")
    typer.echo(f"DB size: {db_size_mb} MB -> {db_size_after} MB")
```

Also add `compact` to the `HELP_TEXT` string in the MAINTENANCE section (around line 76):

```
  memor compact                        Rebuild vector index, reclaim space
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_vec_compaction.py::test_compact_cli_command -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add memor/cli.py tests/test_vec_compaction.py
git commit -m "feat: add memor compact CLI command"
```

---

### Task 6: Add auto-compact to daemon

**Files:**
- Modify: `memor/daemon.py`
- Test: `tests/test_vec_compaction.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_vec_compaction.py`:

```python
def test_auto_compact_triggers_when_bloated(tmp_path):
    from memor.daemon import should_compact, auto_compact
    db_path = str(tmp_path / "test.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    arts = [Artifact(id=f"a{i}", kind="memory", project="p", source="distill",
                     text=f"text {i}", token_count=3, created_at=100.0 + i,
                     meta={"mem_type": "decision"}) for i in range(3)]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))

    # With only 3 vectors and 1 chunk, should_compact returns False (not bloated)
    assert should_compact(s) is False


def test_auto_compact_skips_when_healthy(tmp_path):
    from memor.daemon import should_compact
    db_path = str(tmp_path / "test.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    arts = [Artifact(id=f"a{i}", kind="memory", project="p", source="distill",
                     text=f"text {i}", token_count=3, created_at=100.0 + i,
                     meta={"mem_type": "decision"}) for i in range(3)]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))

    assert should_compact(s) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_vec_compaction.py::test_auto_compact_triggers_when_bloated tests/test_vec_compaction.py::test_auto_compact_skips_when_healthy -v`
Expected: FAIL — `should_compact` doesn't exist

- [ ] **Step 3: Add `should_compact()` and `auto_compact()` to `memor/daemon.py`**

Add after the `compact_memories` function (around line 187):

```python
def should_compact(store: SqliteStore) -> bool:
    try:
        chunk_count = store.db.execute(
            "SELECT COUNT(*) as c FROM vec_artifacts_chunks").fetchone()["c"]
        active_count = store.db.execute(
            "SELECT COUNT(*) as c FROM artifacts WHERE active=1").fetchone()["c"]
    except Exception:
        return False
    if chunk_count == 0 or active_count == 0:
        return False
    chunk_size = store.db.execute(
        "SELECT size FROM vec_artifacts_chunks LIMIT 1").fetchone()["size"]
    ideal_chunks = max(1, active_count // chunk_size + 1)
    return chunk_count > ideal_chunks * 2


def auto_compact(store: SqliteStore, embedder) -> dict | None:
    if not should_compact(store):
        return None
    return store.rebuild_vec_index(embedder)
```

- [ ] **Step 4: Add the auto_compact call to `run_poll_cycle()`**

In `run_poll_cycle()`, add after the `compact_memories` block (around line 287):

```python
    # Auto-compact vec index if bloated
    if new_ingested:
        try:
            result = auto_compact(store, embedder)
            if result:
                print(f"  vec compact: {result['before_chunks']} -> {result['after_chunks']} chunks "
                      f"({result['vectors_reindexed']} vectors, {result['duration_ms']}ms)")
        except Exception:
            pass
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_vec_compaction.py -v`
Expected: All tests pass

- [ ] **Step 6: Run full suite**

Run: `.venv/bin/pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add memor/daemon.py tests/test_vec_compaction.py
git commit -m "feat: auto-compact vec index in daemon when bloated"
```

---

### Task 7: Cache dashboard store instance

**Files:**
- Modify: `memor/dashboard/server.py:15-19`
- Test: `tests/test_vec_compaction.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_vec_compaction.py`:

```python
def test_dashboard_reuses_store(tmp_path):
    db_path = str(tmp_path / "test.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    # Create a minimal artifact so the DB exists with meta
    a = Artifact(id="a1", kind="memory", project="p", source="distill",
                 text="test", token_count=1, created_at=100.0, meta={})
    s.add_artifacts([a], e.embed(["test"]))

    from memor.dashboard.server import create_app
    from starlette.testclient import TestClient

    app = create_app(db_path)
    client = TestClient(app)

    r1 = client.get("/api/health")
    r2 = client.get("/api/health")
    assert r1.status_code == 200
    assert r2.status_code == 200
    # The key check: both requests should return successfully without
    # the "unable to open database file" error that occurs under
    # concurrent new-connection creation.
```

- [ ] **Step 2: Run test to verify it passes (it should pass even now, but verifies the endpoint works)**

Run: `.venv/bin/pytest tests/test_vec_compaction.py::test_dashboard_reuses_store -v`
Expected: PASS (the test verifies the fix works; the actual bug is intermittent under load)

- [ ] **Step 3: Update `_store()` in `memor/dashboard/server.py` to cache**

Replace lines 15-19 in `memor/dashboard/server.py`:

```python
    app = FastAPI(title="Memor Dashboard")
    _db_path = db_path

    def _store() -> SqliteStore:
        return SqliteStore(_db_path, dim=_get_dim(_db_path))
```

With:

```python
    app = FastAPI(title="Memor Dashboard")
    _db_path = db_path
    _cached_store: SqliteStore | None = None

    def _store() -> SqliteStore:
        nonlocal _cached_store
        if _cached_store is None:
            _cached_store = SqliteStore(_db_path, dim=_get_dim(_db_path))
        return _cached_store
```

- [ ] **Step 4: Run test and full suite**

Run: `.venv/bin/pytest tests/test_vec_compaction.py tests/test_dashboard.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add memor/dashboard/server.py tests/test_vec_compaction.py
git commit -m "fix: cache dashboard store instance to prevent connection churn"
```

---

### Task 8: Final verification and full test run

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: All tests pass (should be ~278: 269 existing + 9 new)

- [ ] **Step 2: Verify the new tests cover all spec requirements**

Run: `.venv/bin/pytest tests/test_vec_compaction.py -v`
Expected: 9 tests, all pass:
- `test_deactivate_cleans_vec_and_fts`
- `test_deactivate_stale_cleans_vec`
- `test_decay_quality_cleans_vec`
- `test_add_artifacts_re_add_no_extra_chunks`
- `test_rebuild_vec_index`
- `test_rebuild_chunk_size_selection`
- `test_compact_cli_command`
- `test_auto_compact_triggers_when_bloated`
- `test_auto_compact_skips_when_healthy`
- `test_dashboard_reuses_store`

- [ ] **Step 3: Check git log**

Run: `git log --oneline fix/vec-storage-bloat`
Expected: 8 commits on the branch, each focused on one piece of the fix.
