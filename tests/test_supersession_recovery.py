import time
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact

def _seed(tmp_path):
    now = time.time()
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    arts = [Artifact(id=i, kind="memory", project="p", source="s", text=i,
                     token_count=1, created_at=now, meta={"mem_type": "decision"})
            for i in ("old", "new")]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    s.add_dispute("old", "new", now); s.recompute_validity("old")
    return s

def test_recall_does_not_recover(tmp_path):
    s = _seed(tmp_path)
    s.record_recall(["old"]); s.record_recall(["old"])
    assert s.get_validity_scores(["old"]) == {"old": 0.5}   # recall != use

def test_two_uses_make_dispute_dormant_and_recover(tmp_path):
    s = _seed(tmp_path)
    s.record_usage(["old"])          # affirmation 1
    assert s.get_validity_scores(["old"]) == {"old": 0.5}
    s.record_usage(["old"])          # affirmation 2 => dormant
    assert s.active_disputers("old") == []
    assert s.get_validity_scores(["old"]) == {"old": 1.0}
