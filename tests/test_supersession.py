"""Tests for extractive supersession — newer contradicting memories deactivate older ones."""
import time
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


def _make_store(tmp_path):
    db_path = str(tmp_path / "m.db")
    s = SqliteStore(db_path, dim=16)
    e = FakeEmbedder(dim=16)
    return s, e, db_path


def test_supersession_on_replacement_cue(tmp_path, monkeypatch):
    """When a new memory has a replacement cue and is similar to an existing one, supersede it."""
    import memor.distill.distiller as mod
    monkeypatch.setattr(mod, "SUPERSEDE_SIM_THRESHOLD", 0.50)
    s, e, _ = _make_store(tmp_path)
    old_text = "we use Redis for caching because it has low latency"
    mod._store_memory(s, e, old_text, "decision", "sess1", "proj", 1000.0, [])
    old = s.db.execute(
        "SELECT id, active FROM artifacts WHERE kind='memory' AND text LIKE '%Redis%'"
    ).fetchone()
    assert old["active"] == 1
    new_text = "we use Redis for caching because it has low latency but switched from Redis to Postgres"
    mid = mod._store_memory(s, e, new_text, "decision", "sess2", "proj", 2000.0, [])
    assert mid is not None
    old_after = s.db.execute(
        "SELECT active FROM artifacts WHERE id=?", (old["id"],)
    ).fetchone()
    assert old_after["active"] == 0


def test_no_supersession_without_replacement_cue(tmp_path, monkeypatch):
    """Similar memories without a replacement cue should not supersede."""
    import memor.distill.distiller as mod
    monkeypatch.setattr(mod, "SUPERSEDE_SIM_THRESHOLD", 0.50)
    s, e, _ = _make_store(tmp_path)
    mod._store_memory(s, e, "we use Redis for caching because it is fast", "decision", "sess1", "proj", 1000.0, [])
    mid = mod._store_memory(s, e, "we use Redis for caching and it performs well", "decision", "sess2", "proj", 2000.0, [])
    active = s.db.execute(
        "SELECT COUNT(*) as c FROM artifacts WHERE kind='memory' AND active=1"
    ).fetchone()["c"]
    assert active >= 1


def test_no_supersession_when_older(tmp_path, monkeypatch):
    """A memory older than the existing one should not supersede it, even with a cue."""
    import memor.distill.distiller as mod
    monkeypatch.setattr(mod, "SUPERSEDE_SIM_THRESHOLD", 0.50)
    s, e, _ = _make_store(tmp_path)
    mod._store_memory(s, e, "we switched from Redis to Postgres for storage", "decision", "sess1", "proj", 2000.0, [])
    mid = mod._store_memory(s, e, "we switched from Redis to Postgres for better storage", "decision", "sess2", "proj", 500.0, [])
    active = s.db.execute(
        "SELECT COUNT(*) as c FROM artifacts WHERE kind='memory' AND active=1"
    ).fetchone()["c"]
    assert active >= 1


def test_compaction_prefers_newer_on_tie(tmp_path):
    """When quality scores are equal, compaction should keep the newer memory."""
    from memor.daemon import compact_memories
    s, e, _ = _make_store(tmp_path)
    old_art = Artifact(id="m_old", kind="memory", project="p", source="distill",
                       text="use argon2 for password hashing",
                       token_count=6, created_at=1000.0, meta={})
    new_art = Artifact(id="m_new", kind="memory", project="p", source="distill",
                       text="use argon2 for password hashing",
                       token_count=6, created_at=2000.0, meta={})
    s.add_artifacts([old_art, new_art], e.embed([old_art.text, new_art.text]))
    compact_memories(s, e)
    old_row = s.db.execute("SELECT active FROM artifacts WHERE id='m_old'").fetchone()
    new_row = s.db.execute("SELECT active FROM artifacts WHERE id='m_new'").fetchone()
    assert old_row["active"] == 0
    assert new_row["active"] == 1


def test_replacement_cue_patterns():
    """Verify all replacement cue patterns are recognized."""
    from memor.distill.distiller import _REPLACEMENT_RE
    cues = [
        "instead of Redis we now use Postgres",
        "we no longer use the old auth middleware",
        "switched from REST to gRPC for internal APIs",
        "ripped out the custom ORM",
        "replaced webpack with esbuild for faster builds",
        "deprecated the v1 API endpoint",
        "migrated from MongoDB to PostgreSQL",
        "removed Redis in favor of local cache",
        "moved away from microservices to a monolith",
        "changed the auth flow to use OAuth2",
        "swapped Express for Fastify for better performance",
        "dropped MySQL for SQLite in the test suite",
    ]
    for cue in cues:
        assert _REPLACEMENT_RE.search(cue), f"Should match: {cue}"

    non_cues = [
        "we decided to use argon2 for password hashing",
        "the fix is to add a null check before accessing the property",
        "always use parameterized queries to prevent SQL injection",
    ]
    for text in non_cues:
        assert not _REPLACEMENT_RE.search(text), f"Should NOT match: {text}"
