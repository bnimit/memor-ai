import time
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


def test_backfill_marks_disputes(tmp_path, monkeypatch):
    now = time.time()
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    old = Artifact(id="old", kind="memory", project="p", source="s",
                   text="we use react 17", token_count=4, created_at=now-86400,
                   meta={"mem_type": "decision"})
    new = Artifact(id="new", kind="memory", project="p", source="s",
                   text="we use react 18", token_count=4, created_at=now,
                   meta={"mem_type": "decision"})
    s.add_artifacts([old, new], e.embed([old.text, new.text]))
    # force in-band similarity for any pair
    monkeypatch.setattr(s, "search",
                        lambda vec, scope, k: [(old, 0.85), (new, 0.85)])
    n = s.backfill_disputes(e)
    assert n >= 1
    assert s.get_validity_scores(["old"]) == {"old": 0.5}


def test_backfill_idempotent(tmp_path, monkeypatch):
    now = time.time()
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    old = Artifact(id="old", kind="memory", project="p", source="s", text="a",
                   token_count=1, created_at=now-86400, meta={"mem_type": "decision"})
    new = Artifact(id="new", kind="memory", project="p", source="s", text="a",
                   token_count=1, created_at=now, meta={"mem_type": "decision"})
    s.add_artifacts([old, new], e.embed([old.text, new.text]))
    monkeypatch.setattr(s, "search", lambda vec, scope, k: [(old, 0.85), (new, 0.85)])
    s.backfill_disputes(e)
    rows_first = s.db.execute("SELECT COUNT(*) c FROM disputes").fetchone()["c"]
    s.backfill_disputes(e)   # re-run
    rows_second = s.db.execute("SELECT COUNT(*) c FROM disputes").fetchone()["c"]
    assert rows_first == rows_second   # INSERT OR IGNORE => no duplicates


def test_get_set_meta(tmp_path):
    s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    assert s.get_meta("disputes_backfilled") is None
    assert s.get_meta("disputes_backfilled", default="0") == "0"
    s.set_meta("disputes_backfilled", "1")
    assert s.get_meta("disputes_backfilled") == "1"
    # set_meta is idempotent (INSERT OR REPLACE)
    s.set_meta("disputes_backfilled", "2")
    assert s.get_meta("disputes_backfilled") == "2"
