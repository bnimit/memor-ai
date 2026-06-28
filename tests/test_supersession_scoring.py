import time
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.retrieve.retriever import Retriever
from memor.types import Artifact, Scope

def _mem(id, text, created):
    return Artifact(id=id, kind="memory", project="p", source="s", text=text,
                    token_count=len(text.split()), created_at=created,
                    meta={"mem_type": "decision"})

def _seed(tmp_path):
    now = time.time()
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    arts = [_mem("old", "react version policy", now-86400),
            _mem("new", "react version policy", now)]   # identical text => both match
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    s.add_dispute("old", "new", now); s.recompute_validity("old")
    return e, s

def test_candidate_drop_when_disputer_present(tmp_path):
    e, s = _seed(tmp_path)
    r = Retriever(s, e, k=8, edge_expand=False, supersession=True)
    ids = [h.artifact.id for h in r.query("react version", Scope(project="p")).hits]
    assert "old" not in ids and "new" in ids        # disputer present => drop old

def test_soft_demote_when_disputer_absent(tmp_path):
    e, s = _seed(tmp_path)
    r = Retriever(s, e, k=8, edge_expand=False, supersession=True)
    # exclude the disputer from candidates by querying with k=1 won't help (both match);
    # instead deactivate new so only old is a candidate
    s.db.execute("UPDATE artifacts SET active=0 WHERE id='new'"); s.db.commit()
    hits = r.query("react version", Scope(project="p")).hits
    assert [h.artifact.id for h in hits] == ["old"]              # still retrievable
    assert hits[0].components.get("validity") == 0.5            # but demoted

def test_disabled_keeps_old(tmp_path):
    e, s = _seed(tmp_path)
    r = Retriever(s, e, k=8, edge_expand=False, supersession=False)
    ids = [h.artifact.id for h in r.query("react version", Scope(project="p")).hits]
    assert "old" in ids
