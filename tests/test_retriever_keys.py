# tests/test_retriever_keys.py
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.retrieve.retriever import Retriever
from memor.types import Artifact, Scope

def _mem(s, e, mid, value, keys):
    a = Artifact(id=mid, kind="memory", project="p", source="distill",
                 text=value, token_count=3, created_at=1000.0,
                 meta={"mem_type": "fact", "fact": keys[0][1]})
    s.add_artifacts([a], e.embed([value]))
    s.add_keys(mid, keys, e.embed([kt for _, kt in keys]))

def test_query_matches_via_question_key_returns_value(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    _mem(s, e, "m1", "The auth system uses signed session cookies.",
         [("fact", "auth uses session cookies"), ("question", "how does login work")])
    r = Retriever(s, e, k=3, edge_expand=False, use_keys=True)
    tr = r.query("how does login work", Scope(project="p"))
    assert tr.hits and tr.hits[0].artifact.id == "m1"
    assert tr.hits[0].artifact.text.startswith("The auth system")

def test_use_keys_false_unchanged(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    a = Artifact(id="x", kind="memory", project="p", source="t",
                 text="auth refresh token loop", token_count=4, created_at=1000.0, meta={})
    s.add_artifacts([a], e.embed([a.text]))
    r = Retriever(s, e, k=3, edge_expand=False, use_keys=False)
    tr = r.query("auth refresh", Scope(project="p"))
    assert tr.hits[0].artifact.id == "x"
