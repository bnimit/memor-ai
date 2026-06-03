from memorable.store.sqlite_store import SqliteStore
from memorable.embed.fake import FakeEmbedder
from memorable.eval.runner import run_ablation, run_contradiction_eval
from memorable.types import Artifact

def mk(i,text,t,kind="session_chunk"): return Artifact(id=i,kind=kind,project="p",source="t",
    text=text,token_count=len(text.split()),created_at=t,meta={})

def test_edge_expansion_ablation_reports_both(tmp_path):
    e=FakeEmbedder(dim=16); s=SqliteStore(str(tmp_path/"m.db"),dim=16)
    s.add_artifacts([mk("bug","emscripten sync crash",100),mk("fix","added mutex to queue",100)],
                    e.embed(["emscripten sync crash","added mutex to queue"]))
    s.add_edge("bug","fix","fixes")
    res = run_ablation(query="emscripten sync crash", project="p",
                       relevant_ids={"fix"}, store=s, embedder=e, k=2)
    assert "similarity-only" in res and "similarity+edges" in res
    assert res["similarity+edges"]["recall@k"] >= res["similarity-only"]["recall@k"]

def test_contradiction_eval_prefers_new(tmp_path):
    e=FakeEmbedder(dim=16); s=SqliteStore(str(tmp_path/"m.db"),dim=16)
    s.add_artifacts([mk("old","use bcrypt",10,kind="memory")], e.embed(["use bcrypt"]))
    s.add_artifacts([mk("new","use argon2",20,kind="memory")], e.embed(["use argon2"]))
    s.deactivate("old", superseded_by="new")
    ok = run_contradiction_eval(query="hashing algorithm", project="p",
                                stale_id="old", current_id="new", store=s, embedder=e, k=5)
    assert ok is True   # returns current, suppresses stale
