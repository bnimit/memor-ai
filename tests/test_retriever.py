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


# --- distilled-aware retrieval: widened candidate pool ---

def test_default_kind_weight_is_conservative(tmp_path):
    # Widening the pool is the proven lever; the kind reweight stays a tunable
    # knob, off by default (raising it regressed RETRIEVAL_MISS in the ablation).
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    r = Retriever(s, e, k=2)
    assert abs(r.w_kind - 0.15) < 1e-9


def test_widened_pool_exposes_more_candidates(tmp_path):
    e = FakeEmbedder(dim=64)
    s = SqliteStore(str(tmp_path / "m.db"), dim=64)
    arts = [make(f"c{i}", f"auth token item{i}", 100) for i in range(30)]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    r = Retriever(s, e, k=2, edge_expand=False)
    trace = r.query("auth token", Scope(project="stablex"))
    # Final cut is k=2, but the blend must see a wide pool, not just top-2.
    assert trace.candidates >= 10
