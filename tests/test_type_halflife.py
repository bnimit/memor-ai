"""Type-aware quality decay behind MEMOR_TYPE_HALFLIFE."""
from __future__ import annotations

import time

from memor.embed.fake import FakeEmbedder
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact


def _add(store, e, *, mid, mem_type, created, last_recalled=None, quality=1.0):
    art = Artifact(
        id=mid, kind="memory", project="p", source="t",
        text=f"memory {mid}", token_count=3, created_at=created,
        meta={"mem_type": mem_type},
    )
    store.add_artifacts([art], e.embed([art.text]))
    now = time.time()
    store.db.execute(
        "INSERT OR REPLACE INTO memory_quality"
        "(artifact_id, recall_count, use_count, negative_count, "
        "last_recalled, quality_score, last_decayed_at, validity) "
        "VALUES(?,1,0,0,?,?,NULL,1.0)",
        (mid, last_recalled, quality),
    )
    store.db.commit()


def test_flag_off_uses_uniform_14d(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMOR_TYPE_HALFLIFE", raising=False)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    e = FakeEmbedder(dim=16)
    now = time.time()
    # Decision not recalled for 20 days — under uniform 14d it decays;
    # under 90d half-life it would not.
    _add(s, e, mid="d1", mem_type="decision", created=now - 200 * 86400,
         last_recalled=now - 20 * 86400, quality=1.0)
    n = s.decay_quality(stale_days=14)
    assert n == 1
    q = s.db.execute(
        "SELECT quality_score FROM memory_quality WHERE artifact_id='d1'"
    ).fetchone()["quality_score"]
    assert q == 0.5


def test_flag_on_spares_durable_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOR_TYPE_HALFLIFE", "1")
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    e = FakeEmbedder(dim=16)
    now = time.time()
    # Decision half-life 90d: 20d idle must not decay.
    _add(s, e, mid="d1", mem_type="decision", created=now - 200 * 86400,
         last_recalled=now - 20 * 86400, quality=1.0)
    # Extract half-life 21d: 30d idle must decay.
    _add(s, e, mid="e1", mem_type="extract", created=now - 200 * 86400,
         last_recalled=now - 30 * 86400, quality=1.0)
    n = s.decay_quality(stale_days=14)
    assert n == 1
    d = s.db.execute(
        "SELECT quality_score FROM memory_quality WHERE artifact_id='d1'"
    ).fetchone()["quality_score"]
    ex = s.db.execute(
        "SELECT quality_score FROM memory_quality WHERE artifact_id='e1'"
    ).fetchone()["quality_score"]
    assert d == 1.0
    assert ex == 0.5
