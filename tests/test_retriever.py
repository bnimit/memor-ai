from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.retrieve.retriever import Retriever
from memor.types import Artifact, Scope

def make(id, text, created, kind="session_chunk"):
    return Artifact(id=id, kind=kind, project="stablex", source="t",
                    text=text, token_count=len(text.split()), created_at=created, meta={})

def test_retriever_ranks_and_traces(tmp_path):
    import time as _time
    now = _time.time()
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    arts = [make("a1","auth refresh token loop", now - 3600),       # 1 hour ago
            make("a2","auth refresh token loop", now - 86400 * 30)] # 30 days ago
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    r = Retriever(s, e, k=2, recency_weight=0.3, edge_expand=False)
    trace = r.query("auth refresh", Scope(project="stablex"))
    assert trace.hits[0].artifact.id == "a1"          # recency breaks the tie
    assert "sim" in trace.hits[0].components and "recency" in trace.hits[0].components
    assert trace.latency_ms >= 0 and trace.candidates >= 1

def test_edge_expansion_pulls_linked(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    s.add_artifacts([make("bug","emscripten sync crash",100),
                     make("fix","added mutex around sync queue",100)],
                    e.embed(["emscripten sync crash","added mutex around sync queue"]))
    s.add_edge("bug","fix","fixes")
    r = Retriever(s, e, k=2, edge_expand=True)
    trace = r.query("emscripten sync crash", Scope(project="stablex"))
    ids = [h.artifact.id for h in trace.hits]
    assert "fix" in ids        # surfaced via edge even though query didn't match its words


# --- reaffirmation recency (effective_ts = max(created_at, last_reaffirmed)) ---

def test_reaffirmed_memory_outranks_equally_similar_quiet_one(tmp_path):
    import time as _t
    now = _t.time(); old = now - 86400 * 60      # both created 60 days ago
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.add_artifacts([make("quiet", "auth token refresh", old, kind="memory"),
                     make("fresh", "auth token refresh", old, kind="memory")],
                    e.embed(["auth token refresh", "auth token refresh"]))
    s.reaffirm(["fresh"], now)                    # fresh re-observed recently
    r = Retriever(s, e, k=2, edge_expand=False)
    trace = r.query("auth token refresh", Scope(project="stablex"))
    assert trace.hits[0].artifact.id == "fresh"  # reaffirmation lifts it over the quiet one


def test_unreaffirmed_recency_is_unchanged(tmp_path):
    import time as _t, math
    now = _t.time(); old = now - 86400 * 7        # 7 days, NULL last_reaffirmed
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.add_artifacts([make("m", "auth token", old, kind="memory")], e.embed(["auth token"]))
    r = Retriever(s, e, k=1, edge_expand=False)
    h = r.query("auth token", Scope(project="stablex")).hits[0]
    expected = math.exp(-0.693 * 7 / 14)          # decays from created_at, as before
    assert abs(h.components["recency"] - round(expected, 3)) < 0.02
