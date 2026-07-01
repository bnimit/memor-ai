from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact, Scope

def _tables(s):
    return {r[0] for r in s.db.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()}

def test_key_vector_tables_exist(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    t = _tables(s)
    assert "key_vectors" in t and "vec_keys" in t and "fts_keys" in t

def test_migration_idempotent_on_reopen(tmp_path):
    p = str(tmp_path / "m.db")
    SqliteStore(p, dim=16)
    s2 = SqliteStore(p, dim=16)  # must not raise on second open
    assert "key_vectors" in _tables(s2) and "vec_keys" in _tables(s2) and "fts_keys" in _tables(s2)

def _mem(s, e, mid, text, project="p"):
    a = Artifact(id=mid, kind="memory", project=project, source="t",
                 text=text, token_count=2, created_at=1000.0, meta={})
    s.add_artifacts([a], e.embed([text]))

def test_add_and_search_keys_resolve_to_memory(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    _mem(s, e, "m1", "auth uses session cookies")
    s.add_keys("m1", [("fact", "auth uses session cookies"),
                      ("question", "how does auth work")],
               e.embed(["auth uses session cookies", "how does auth work"]))
    hits = s.search_keys(e.embed(["session cookies auth"])[0], Scope(project="p"), k=5)
    assert hits and hits[0][0] == "m1"
    assert s.count_keys() == 2

def test_search_keys_excludes_inactive_memory(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    _mem(s, e, "m1", "auth uses session cookies")
    s.add_keys("m1", [("fact", "auth uses session cookies")],
               e.embed(["auth uses session cookies"]))
    s.deactivate("m1", superseded_by="m2")
    hits = s.search_keys(e.embed(["session cookies auth"])[0], Scope(project="p"), k=5)
    assert all(mid != "m1" for mid, _ in hits)

def test_delete_keys(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    _mem(s, e, "m1", "x")
    s.add_keys("m1", [("fact", "x")], e.embed(["x"]))
    s.delete_keys("m1")
    assert s.count_keys() == 0
