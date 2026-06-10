"""Tests for vec storage bloat fix: deactivation cleanup, rebuild, auto-compact."""
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


def test_deactivate_cleans_vec_and_fts(tmp_path):
    s, e, arts = _make_store(tmp_path)
    vec_count_before = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    fts_count_before = s.db.execute("SELECT COUNT(*) as c FROM fts_artifacts").fetchone()["c"]
    assert vec_count_before == 5
    assert fts_count_before == 5

    s.deactivate("art-2", superseded_by="art-3")

    vec_count_after = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    fts_count_after = s.db.execute("SELECT COUNT(*) as c FROM fts_artifacts").fetchone()["c"]
    assert vec_count_after == 4
    assert fts_count_after == 4

    row = s.db.execute("SELECT active FROM artifacts WHERE id='art-2'").fetchone()
    assert row["active"] == 0


def test_deactivate_stale_cleans_vec(tmp_path):
    s, e, arts = _make_store(tmp_path)
    vec_before = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert vec_before == 5

    count = s.deactivate_stale(days=0)
    assert count > 0

    vec_after = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert vec_after < vec_before


def test_decay_quality_cleans_vec(tmp_path):
    s, e, arts = _make_store(tmp_path)
    for a in arts[:2]:
        s.db.execute(
            "INSERT INTO memory_quality(artifact_id, recall_count, use_count, quality_score, last_recalled) "
            "VALUES(?, 10, 0, 0.02, ?)", (a.id, 1.0))
    s.db.commit()

    vec_before = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert vec_before == 5

    decayed = s.decay_quality(stale_days=0, factor=0.5, deactivate_floor=0.03)
    assert decayed >= 2

    vec_after = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert vec_after < vec_before


def test_add_artifacts_re_add_no_extra_chunks(tmp_path):
    """Re-adding the same artifact should not leak extra vec0 chunk slots."""
    s, e, arts = _make_store(tmp_path, n=3)
    rowids_before = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert rowids_before == 3

    vecs = e.embed([a.text for a in arts])
    s.add_artifacts(arts, vecs)

    rowids_after = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert rowids_after == 3


def test_rebuild_vec_index(tmp_path):
    s, e, arts = _make_store(tmp_path, n=10)
    for a in arts[:5]:
        s.deactivate(a.id, superseded_by=arts[5].id)

    active_before = s.db.execute(
        "SELECT COUNT(*) as c FROM artifacts WHERE active=1").fetchone()["c"]
    assert active_before == 5

    result = s.rebuild_vec_index(e)
    assert result["vectors_reindexed"] == 5

    vec_count = s.db.execute("SELECT COUNT(*) as c FROM vec_artifacts_rowids").fetchone()["c"]
    assert vec_count == 5

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
