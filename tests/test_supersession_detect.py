import time
from memor.supersession import should_dispute, find_disputes, BAND_LOW, BAND_HIGH
from memor.types import Artifact

NOW = time.time()
def kw(**o):
    base = dict(sim=0.85, new_type="decision", old_type="extract",
                new_created=NOW, old_created=NOW-86400,
                new_quality=0.6, old_quality=0.5)
    base.update(o); return base

def test_disputes_cross_type_in_band():
    assert should_dispute(**kw()) is True                       # decision vs extract OK

def test_dedup_band_excluded():
    assert should_dispute(**kw(sim=BAND_HIGH)) is False         # >=0.92 is dedup
def test_unrelated_excluded():
    assert should_dispute(**kw(sim=BAND_LOW - 0.01)) is False   # <0.80 unrelated

def test_snippet_never_disputes():
    assert should_dispute(**kw(new_type="snippet")) is False
    assert should_dispute(**kw(old_type="snippet")) is False

def test_only_newer_disputes_older():
    assert should_dispute(**kw(new_created=NOW-2*86400, old_created=NOW)) is False

def test_quality_guard_blocks_junk_disputer():
    assert should_dispute(**kw(new_quality=0.4)) is False               # disputer < 0.5
    assert should_dispute(**kw(new_quality=0.5, old_quality=0.7)) is False  # >0.1 worse

def test_find_disputes_persists(tmp_path, monkeypatch):
    from memor.store.sqlite_store import SqliteStore
    from memor.embed.fake import FakeEmbedder
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    old = Artifact(id="old", kind="memory", project="p", source="s",
                   text="we use react 17", token_count=4, created_at=NOW-86400,
                   meta={"mem_type": "decision"})
    s.add_artifacts([old], e.embed([old.text]))
    new = Artifact(id="new", kind="memory", project="p", source="s",
                   text="we use react 18 now", token_count=5, created_at=NOW,
                   meta={"mem_type": "decision"})
    s.add_artifacts([new], e.embed([new.text]))
    # Force the similarity into the dispute band regardless of FakeEmbedder geometry.
    monkeypatch.setattr(s, "search", lambda vec, scope, k: [(old, 0.85)])
    disputed = find_disputes(s, e, new, new_quality=0.6)
    assert disputed == ["old"]
    assert s.get_validity_scores(["old"]) == {"old": 0.5}
