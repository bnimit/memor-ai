import time
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.retrieve.retriever import Retriever
from memor.types import Artifact, Scope

def _mem(id, text, created, mem_type):
    return Artifact(id=id, kind="memory", project="p", source="s", text=text,
                    token_count=len(text.split()), created_at=created,
                    meta={"mem_type": mem_type})

def test_durable_type_outranks_volatile_at_same_age_when_enabled(tmp_path):
    now = time.time()
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    # identical text => identical similarity; both 40 days old.
    arts = [_mem("dec", "postgres chosen for storage", now - 40*86400, "decision"),
            _mem("obs", "postgres chosen for storage", now - 40*86400, "extract")]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    r = Retriever(s, e, k=2, edge_expand=False, type_halflife=True)
    hits = r.query("postgres storage", Scope(project="p")).hits
    # decision (90d half-life) keeps more recency boost than extract (21d) => ranks first
    assert hits[0].artifact.id == "dec"

def test_uniform_when_disabled(tmp_path):
    now = time.time()
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    arts = [_mem("dec", "postgres chosen for storage", now - 40*86400, "decision"),
            _mem("obs", "postgres chosen for storage", now - 40*86400, "extract")]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    r = Retriever(s, e, k=2, edge_expand=False, type_halflife=False)
    hits = r.query("postgres storage", Scope(project="p")).hits
    # same age, same uniform half-life => recency equal => scores tie (order stable by dict)
    assert abs(hits[0].components["recency"] - hits[1].components["recency"]) < 1e-9
