import time, json
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact

def _mem(id, mem_type, age_days):
    now = time.time()
    return Artifact(id=id, kind="memory", project="p", source="s", text=id,
                    token_count=1, created_at=now - age_days*86400,
                    meta={"mem_type": mem_type})

def test_decay_respects_per_type_window(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    # both 30 days old, never recalled
    arts = [_mem("dec", "decision", 30), _mem("obs", "extract", 30)]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    # seed quality rows so decay has something to halve
    s.record_recall(["dec", "obs"])
    s.db.execute("UPDATE memory_quality SET last_recalled=NULL")  # force "stale"
    s.db.commit()
    s.decay_quality(factor=0.5, deactivate_floor=0.03)
    # extract: 21d window, 30d old => decayed below 0.5
    assert s.get_quality_score("obs") < 0.5
    # decision: 90d window, 30d old => NOT yet stale => unchanged
    assert s.get_quality_score("dec") == 0.5
