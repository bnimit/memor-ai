"""Tests for Phase 4 feedback loop: quality tracking, usage detection, compaction."""
import json
import time
from pathlib import Path
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


def _make_store(tmp_path):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    return s, e, db_path


def test_record_recall_creates_quality_entry(tmp_path):
    s, e, _ = _make_store(tmp_path)
    art = Artifact(id="m1", kind="memory", project="p", source="distill",
                   text="use argon2 for hashing", token_count=6, created_at=100.0, meta={})
    s.add_artifacts([art], e.embed([art.text]))
    s.record_recall(["m1"])
    row = s.db.execute("SELECT * FROM memory_quality WHERE artifact_id='m1'").fetchone()
    assert row["recall_count"] == 1
    assert row["use_count"] == 0
    assert row["quality_score"] == 0.5


def test_record_recall_increments(tmp_path):
    s, e, _ = _make_store(tmp_path)
    s.record_recall(["m1"])
    s.record_recall(["m1"])
    row = s.db.execute("SELECT * FROM memory_quality WHERE artifact_id='m1'").fetchone()
    assert row["recall_count"] == 2


def test_record_usage_updates_quality(tmp_path):
    s, e, _ = _make_store(tmp_path)
    s.record_recall(["m1"])
    s.record_recall(["m1"])
    s.record_recall(["m1"])
    s.record_usage(["m1"])
    row = s.db.execute("SELECT * FROM memory_quality WHERE artifact_id='m1'").fetchone()
    assert row["use_count"] == 1
    assert row["quality_score"] > 0.3


def test_quality_score_bayesian(tmp_path):
    s, e, _ = _make_store(tmp_path)
    s.record_recall(["m1"])
    s.record_usage(["m1"])
    row = s.db.execute("SELECT quality_score FROM memory_quality WHERE artifact_id='m1'").fetchone()
    # (1+1)/(1+2) = 0.667
    assert abs(row["quality_score"] - 0.667) < 0.01


def test_get_quality_score_default(tmp_path):
    s, e, _ = _make_store(tmp_path)
    assert s.get_quality_score("nonexistent") == 0.5


def test_get_stale_memories(tmp_path):
    s, e, _ = _make_store(tmp_path)
    old_time = time.time() - (60 * 86400)  # 60 days ago
    art = Artifact(id="old1", kind="memory", project="p", source="distill",
                   text="ancient decision", token_count=3, created_at=old_time, meta={})
    s.add_artifacts([art], e.embed([art.text]))
    stale = s.get_stale_memories(days=30)
    assert "old1" in stale


def test_stale_excludes_recently_recalled(tmp_path):
    s, e, _ = _make_store(tmp_path)
    old_time = time.time() - (60 * 86400)
    art = Artifact(id="old2", kind="memory", project="p", source="distill",
                   text="old but still used", token_count=5, created_at=old_time, meta={})
    s.add_artifacts([art], e.embed([art.text]))
    s.record_recall(["old2"])  # just recalled now
    stale = s.get_stale_memories(days=30)
    assert "old2" not in stale


def test_deactivate_stale(tmp_path):
    s, e, _ = _make_store(tmp_path)
    old_time = time.time() - (60 * 86400)
    art = Artifact(id="stale1", kind="memory", project="p", source="distill",
                   text="stale memory", token_count=3, created_at=old_time, meta={})
    s.add_artifacts([art], e.embed([art.text]))
    count = s.deactivate_stale(days=30)
    assert count == 1
    row = s.db.execute("SELECT active FROM artifacts WHERE id='stale1'").fetchone()
    assert row["active"] == 0


def test_text_was_used():
    from memor.feedback import _text_was_used
    memory = "we decided to use argon2 for password hashing because it is memory hard and resistant to GPU attacks"
    assistant = ["the authentication module uses argon2 for password hashing because it is memory hard"]
    assert _text_was_used(memory, assistant)


def test_text_was_not_used():
    from memor.feedback import _text_was_used
    memory = "we decided to use argon2 for password hashing because it is memory hard and resistant to GPU attacks"
    assistant = ["I fixed the CSS layout issue by adding flexbox"]
    assert not _text_was_used(memory, assistant)


def test_compact_memories(tmp_path):
    from memor.daemon import compact_memories
    s, e, _ = _make_store(tmp_path)
    art1 = Artifact(id="m1", kind="memory", project="p", source="distill",
                    text="use argon2 for password hashing", token_count=6, created_at=100.0, meta={})
    art2 = Artifact(id="m2", kind="memory", project="p", source="distill",
                    text="use argon2 for password hashing", token_count=6, created_at=200.0, meta={})
    s.add_artifacts([art1, art2], e.embed([art1.text, art2.text]))
    count = compact_memories(s, e)
    assert count >= 1
    active = s.db.execute("SELECT COUNT(*) as c FROM artifacts WHERE kind='memory' AND active=1").fetchone()["c"]
    assert active == 1
