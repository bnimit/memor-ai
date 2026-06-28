import time
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact

def _mem(id, mem_type="decision"):
    return Artifact(id=id, kind="memory", project="p", source="s", text=id,
                    token_count=1, created_at=time.time(), meta={"mem_type": mem_type})

def _store(tmp_path, ids):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    arts = [_mem(i) for i in ids]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    return s

def test_single_dispute_sets_validity_half(tmp_path):
    s = _store(tmp_path, ["old", "new"])
    s.add_dispute("old", "new", time.time())
    assert s.recompute_validity("old") == 0.5
    assert s.get_validity_scores(["old", "new"]) == {"old": 0.5, "new": 1.0}

def test_two_disputers_floor(tmp_path):
    s = _store(tmp_path, ["old", "n1", "n2"])
    s.add_dispute("old", "n1", time.time())
    s.add_dispute("old", "n2", time.time())
    assert s.recompute_validity("old") == 0.25

def test_transitivity_stale_disputer_drops_out(tmp_path):
    s = _store(tmp_path, ["old", "mid", "new"])
    s.add_dispute("old", "mid", time.time())     # mid disputes old
    s.add_dispute("mid", "new", time.time())     # new disputes mid => mid inactive
    s.recompute_validity("old")
    assert s.active_disputers("old") == []        # mid no longer counts
    assert s.get_validity_scores(["old"]).get("old", 1.0) == 1.0

def test_get_active_disputers_batch(tmp_path):
    s = _store(tmp_path, ["old", "new"])
    s.add_dispute("old", "new", time.time())
    assert s.get_active_disputers(["old", "new"]) == {"old": ["new"]}
