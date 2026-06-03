from memorable.store.sqlite_store import SqliteStore
from memorable.types import Artifact, Scope
from memorable.embed.fake import FakeEmbedder

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
