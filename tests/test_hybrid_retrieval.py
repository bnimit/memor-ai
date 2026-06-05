"""Tests for hybrid retrieval (FTS5 + dense, RRF) and the absolute-similarity gate."""
import time as _time

from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.retrieve.retriever import Retriever
from memor.types import Artifact, Scope


def _make(id, text, created, kind="session_chunk", project="stablex"):
    return Artifact(id=id, kind=kind, project=project, source="t",
                    text=text, token_count=len(text.split()), created_at=created, meta={})


class _FixedEmbedder:
    """Maps exact texts to caller-supplied vectors — lets a test produce
    negative-cosine (anti-correlated) candidates, which FakeEmbedder cannot."""
    def __init__(self, mapping, dim):
        self.mapping, self.dim = mapping, dim

    def embed(self, texts):
        return [self.mapping[t] for t in texts]


# ── Increment 1: absolute-similarity gate ──────────────────────────────

def test_min_similarity_drops_anticorrelated_before_blend(tmp_path):
    """A brand-new but semantically-irrelevant memory must not ride recency
    past the gate. The floor applies to raw cosine, before blending."""
    now = _time.time()
    e = FakeEmbedder(dim=32)
    s = SqliteStore(str(tmp_path / "m.db"), dim=32)
    arts = [
        _make("good", "auth refresh token rotation loop", now - 86400 * 30),  # relevant, old
        _make("noise", "completely separate unrelated kitchen content", now),  # irrelevant, brand new
    ]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    r = Retriever(s, e, k=8, min_similarity=0.1, edge_expand=False)
    trace = r.query("auth refresh token", Scope(project="stablex"))
    ids = [h.artifact.id for h in trace.hits]
    assert "good" in ids
    assert "noise" not in ids  # dropped by floor; recency cannot rescue it


def test_default_gate_drops_negative_cosine(tmp_path):
    """The gate must be ON by default: an anti-correlated (negative-cosine)
    candidate is dropped even without an explicit min_similarity."""
    emb = _FixedEmbedder({
        "query vec": [1.0, 0.0],
        "relevant doc": [1.0, 0.0],   # cosine +1
        "opposite doc": [-1.0, 0.0],  # cosine -1 (anti-correlated)
    }, dim=2)
    s = SqliteStore(str(tmp_path / "m.db"), dim=2)
    arts = [_make("rel", "relevant doc", 100), _make("opp", "opposite doc", 100)]
    s.add_artifacts(arts, emb.embed(["relevant doc", "opposite doc"]))
    r = Retriever(s, emb, k=8, edge_expand=False)  # default min_similarity
    trace = r.query("query vec", Scope(project="stablex"))
    ids = [h.artifact.id for h in trace.hits]
    assert "rel" in ids
    assert "opp" not in ids  # negative cosine dropped by the default gate


# ── Increment 2: lexical (FTS5/BM25) search ────────────────────────────

def test_search_lexical_matches_exact_term(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    arts = [
        _make("a1", "the NaN coverage bug appeared in the dashboard", 100),
        _make("a2", "argon2 password hashing for the auth module", 100),
        _make("a3", "unrelated note about generic caching layers", 100),
    ]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    res = s.search_lexical("argon2 hashing", Scope(project="stablex"), k=5)
    ids = [a.id for a, _ in res]
    assert "a2" in ids
    assert ids[0] == "a2"  # strongest BM25 match ranks first


def test_search_lexical_handles_fts_operator_words(tmp_path):
    """Query terms that collide with FTS5 operators (and/or/near) must be
    treated as literals, not syntax — otherwise MATCH raises."""
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.add_artifacts([_make("a1", "argon2 and bcrypt near the auth layer", 100)],
                    e.embed(["argon2 and bcrypt near the auth layer"]))
    res = s.search_lexical("and argon2 or near", Scope(project="stablex"), k=5)
    assert [a.id for a, _ in res] == ["a1"]  # no FTS5 syntax error, still matches


def test_search_lexical_respects_scope_and_active(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    arts = [
        _make("here", "argon2 hashing decision", 100, project="stablex"),
        _make("other", "argon2 hashing decision", 100, project="elsewhere"),
        _make("dead", "argon2 hashing decision", 100, project="stablex"),
    ]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    s.deactivate("dead", superseded_by="here")
    ids = [a.id for a, _ in s.search_lexical("argon2", Scope(project="stablex"), k=5)]
    assert ids == ["here"]  # other project excluded, deactivated excluded


# ── Increment 3: FTS backfill for legacy databases ─────────────────────

def test_rebuild_fts_indexes_existing_rows(tmp_path):
    """A DB created before FTS existed has artifacts but no FTS rows.
    rebuild_fts() backfills the index so lexical search works."""
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.add_artifacts([_make("a1", "argon2 hashing decision", 100)],
                    e.embed(["argon2 hashing decision"]))
    s.db.execute("DELETE FROM fts_artifacts")  # simulate a pre-FTS database
    s.db.commit()
    assert s.search_lexical("argon2", Scope(project="stablex"), k=5) == []
    n = s.rebuild_fts()
    assert n == 1
    ids = [a.id for a, _ in s.search_lexical("argon2", Scope(project="stablex"), k=5)]
    assert ids == ["a1"]


# ── Increment 4: reciprocal rank fusion ────────────────────────────────

def test_rrf_fuse_unions_and_scores():
    from memor.retrieve.retriever import rrf_fuse
    dense = ["x"]            # x only in dense
    lexical = ["y", "x"]     # y only in lexical; x in both
    fused = rrf_fuse([dense, lexical], k=60)
    assert fused["x"] > fused["y"]   # x appears in both lists → higher fused score
    assert "y" in fused              # lexical-only item still surfaces


def test_rrf_fuse_rewards_agreement():
    from memor.retrieve.retriever import rrf_fuse
    dense = ["a", "b", "c"]
    lexical = ["a", "c", "b"]
    fused = rrf_fuse([dense, lexical], k=60)
    ranked = sorted(fused, key=lambda i: fused[i], reverse=True)
    assert ranked[0] == "a"  # rank-1 in both lists wins


# ── Increment 5: hybrid fusion in the retriever ────────────────────────

def test_hybrid_lexical_enriches_when_query_on_topic(tmp_path):
    """When the query is on-topic (dense finds a positive match), the lexical
    channel surfaces an exact-term match the dense channel gated out."""
    emb = _FixedEmbedder({
        "argon2 term": [1.0, 0.0],
        "status update on the project": [1.0, 0.0],   # on-topic, keeps dense non-empty
        "argon2 rare exact term": [-1.0, 0.0],        # dense-gated, but exact lexical match
    }, dim=2)
    s = SqliteStore(str(tmp_path / "m.db"), dim=2)
    arts = [_make("ontopic", "status update on the project", 100),
            _make("marker", "argon2 rare exact term", 100)]
    s.add_artifacts(arts, emb.embed([a.text for a in arts]))
    r = Retriever(s, emb, k=5, edge_expand=False)
    ids = [h.artifact.id for h in r.query("argon2 term", Scope(project="stablex")).hits]
    assert "ontopic" in ids
    assert "marker" in ids  # dense-gated but recovered via the lexical channel


def test_hybrid_suppresses_lexical_when_query_off_topic(tmp_path):
    """If the query is off-topic for the project (dense fully gated), the lexical
    channel is suppressed — a weak term match must not be injected as if
    relevant. This is what stops off-topic OR-matches from leaking."""
    emb = _FixedEmbedder({
        "argon2 term": [0.0, 1.0],                    # orthogonal to everything stored
        "status update on the project": [1.0, 0.0],
        "argon2 rare exact term": [-1.0, 0.0],
    }, dim=2)
    s = SqliteStore(str(tmp_path / "m.db"), dim=2)
    arts = [_make("ontopic", "status update on the project", 100),
            _make("marker", "argon2 rare exact term", 100)]
    s.add_artifacts(arts, emb.embed([a.text for a in arts]))
    r = Retriever(s, emb, k=5, edge_expand=False)
    ids = [h.artifact.id for h in r.query("argon2 term", Scope(project="stablex")).hits]
    assert ids == []  # dense gated to empty → lexical suppressed → nothing injected


# ── Increment 6: recall() carries hybrid + gate end-to-end ─────────────

def test_recall_uses_hybrid_and_gate(tmp_path):
    db = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=64)
    s = SqliteStore(db, dim=64)
    arts = [
        _make("doc", "argon2 password hashing decision", 100, kind="memory"),
        _make("noise", "unrelated kitchen recipe content", 100, kind="memory"),
    ]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    from memor.recall import recall
    res = recall("argon2 hashing", "stablex", db, embedder=e, k=8, threshold=0.0)
    ids = res.hit_ids or []
    assert "doc" in ids        # on-topic dense match, confirmed by lexical
    assert "noise" not in ids  # anti-correlated → gated out


def test_reopen_auto_backfills_empty_fts(tmp_path):
    """A pre-FTS database (artifacts present, FTS empty) backfills itself on the
    next open, so existing installs get lexical search without a re-ingest."""
    db = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db, dim=16)
    s.add_artifacts([_make("a1", "argon2 hashing decision", 100)],
                    e.embed(["argon2 hashing decision"]))
    s.db.execute("DELETE FROM fts_artifacts")  # simulate legacy db
    s.db.commit()
    s.db.close()

    s2 = SqliteStore(db, dim=16)  # reopen triggers migration
    ids = [a.id for a, _ in s2.search_lexical("argon2", Scope(project="stablex"), k=5)]
    assert ids == ["a1"]
