from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact, Scope
from memor.embed.fake import FakeEmbedder

def make(id, project, text, created, kind="session_chunk"):
    return Artifact(id=id, kind=kind, project=project, source="t",
                    text=text, token_count=len(text.split()), created_at=created, meta={})

def test_add_search_scope_and_edges(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    arts = [make("a1","stablex","auth refresh token loop",100),
            make("a2","stablex","emscripten sync bug",90),
            make("a3","other","auth refresh token loop",100)]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))

    q = e.embed(["auth refresh"])[0]
    hits = s.search(q, Scope(project="stablex"), k=5)
    ids = [a.id for a, _ in hits]
    assert "a1" in ids and "a3" not in ids       # scope filter applied
    assert hits[0][0].id == "a1"                  # most similar first

    s.add_edge("a1", "a2", "fixes")
    nbrs = [a.id for a in s.neighbors(["a1"], ["fixes"], hops=1)]
    assert nbrs == ["a2"]

def test_deactivate_excludes_from_search(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.add_artifacts([make("old","p","use library X",10)], e.embed(["use library X"]))
    s.add_artifacts([make("new","p","use library Y instead",20)], e.embed(["use library Y instead"]))
    s.deactivate("old", superseded_by="new")
    ids = [a.id for a, _ in s.search(e.embed(["use library"])[0], Scope(project="p"), k=5)]
    assert "old" not in ids and "new" in ids


# --- KNN-fetch cap + batched quality lookup (safe retrieval wins) ---

def test_search_knn_fetch_capped(tmp_path):
    # A large k must not exceed sqlite-vec's internal knn limit (4096).
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.add_artifacts([make("a", "p", "hello world", 1)], e.embed(["hello world"]))
    hits = s.search(e.embed(["hello world"])[0], Scope(project="p"), k=300)
    assert len(hits) <= 1  # no OperationalError, returns what's available


def test_get_quality_scores_batch_matches_per_id(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.record_recall(["a", "b"])
    s.record_usage(["a"])
    scores = s.get_quality_scores(["a", "b", "missing"])
    assert scores["a"] == s.get_quality_score("a")
    assert scores["b"] == s.get_quality_score("b")
    assert "missing" not in scores
    assert s.get_quality_scores([]) == {}


def test_recall_latency_reports_percentiles_not_just_a_mean(tmp_path):
    """A mean is the wrong summary for latency.

    On a real store one 135-second recall dragged the average to 1,254 ms
    while the median was 176 ms, and the README quoted "Sub-15ms" against
    both. Percentiles describe what a user actually waits for.
    """
    from memor.store.sqlite_store import SqliteStore

    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    # Ninety fast recalls and one pathological outlier.
    for _ in range(90):
        store.log_recall(
            project="p", query_preview="q", hits_count=3, top_score=0.5,
            tokens_injected=100, latency_ms=100.0, status="ok",
            session_id="s", agent="claude")
    store.log_recall(
        project="p", query_preview="q", hits_count=3, top_score=0.5,
        tokens_injected=100, latency_ms=135_000.0, status="ok",
        session_id="s", agent="claude")

    stats = store.get_recall_stats()
    assert stats["p50_latency_ms"] == 100.0
    # The mean is dragged far above every typical recall by the one outlier.
    assert stats["avg_latency_ms"] > 1_000
    assert stats["p50_latency_ms"] < stats["avg_latency_ms"]


def test_recall_latency_percentiles_ignore_misses(tmp_path):
    """A miss returns early and would flatter the figure for the wrong reason."""
    from memor.store.sqlite_store import SqliteStore

    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    for _ in range(10):
        store.log_recall(project="p", query_preview="q", hits_count=0,
                         top_score=0.0, tokens_injected=0, latency_ms=1.0,
                         status="no_hits", session_id="s", agent="claude")
    store.log_recall(project="p", query_preview="q", hits_count=2,
                     top_score=0.5, tokens_injected=50, latency_ms=200.0,
                     status="ok", session_id="s", agent="claude")

    stats = store.get_recall_stats()
    assert stats["p50_latency_ms"] == 200.0, "only successful recalls count"


def test_recall_latency_on_an_empty_store(tmp_path):
    from memor.store.sqlite_store import SqliteStore

    stats = SqliteStore(str(tmp_path / "m.db"), dim=16).get_recall_stats()
    assert stats["p50_latency_ms"] == 0.0
    assert stats["p90_latency_ms"] == 0.0
