"""Tests for soft temporal disputes on distill (no hard deactivate on updates)."""
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact
from memor.retrieve.similarity import cosine_to_stored_sim


def _make_store(tmp_path):
    db_path = str(tmp_path / "m.db")
    s = SqliteStore(db_path, dim=16)
    e = FakeEmbedder(dim=16)
    return s, e, db_path


def test_update_writes_dispute_and_keeps_old_active(tmp_path, monkeypatch):
    """Semantic updates stay active; a dispute row demotes them at recall time."""
    import memor.distill.distiller as mod
    from memor.supersession import find_and_record_disputes

    s, e, _ = _make_store(tmp_path)
    old_text = "we use Redis for caching because it has low latency"
    mod._store_memory(s, e, old_text, "decision", "sess1", "proj", 1000.0, [])
    old = s.db.execute(
        "SELECT id, active FROM artifacts WHERE kind='memory' AND text LIKE '%Redis%'"
    ).fetchone()
    assert old["active"] == 1

    band_sim = cosine_to_stored_sim(0.85)
    old_art = s._row_to_artifact(
        s.db.execute("SELECT * FROM artifacts WHERE id=?", (old["id"],)).fetchone()
    )

    def fake_search(vec, scope, k=8):
        return [(old_art, band_sim)]

    monkeypatch.setattr(s, "search", fake_search)
    new_text = "we switched from Redis to Postgres for caching"
    mid = mod._store_memory(s, e, new_text, "decision", "sess2", "proj", 2000.0, [])
    assert mid is not None
    old_after = s.db.execute(
        "SELECT active FROM artifacts WHERE id=?", (old["id"],)
    ).fetchone()
    assert old_after["active"] == 1
    row = s.db.execute(
        "SELECT * FROM disputes WHERE disputed_id=? AND disputer_id=?",
        (old["id"], mid),
    ).fetchone()
    assert row is not None
    assert s.get_validity(old["id"]) == 0.5


def test_exact_dedup_still_skips_store(tmp_path, monkeypatch):
    import memor.distill.distiller as mod
    from memor.retrieve.similarity import DISPUTE_COSINE_HI, cosine_to_stored_sim

    s, e, _ = _make_store(tmp_path)
    text = "we use argon2 for password hashing"
    mid1 = mod._store_memory(s, e, text, "decision", "sess1", "proj", 1000.0, [])
    assert mid1 is not None
    art = s._row_to_artifact(
        s.db.execute("SELECT * FROM artifacts WHERE id=?", (mid1,)).fetchone()
    )
    monkeypatch.setattr(
        s, "search",
        lambda *a, **k: [(art, cosine_to_stored_sim(DISPUTE_COSINE_HI))],
    )
    mid2 = mod._store_memory(s, e, text + " ", "decision", "sess2", "proj", 2000.0, [])
    assert mid2 is None


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
