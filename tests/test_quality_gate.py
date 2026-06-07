"""Tests for distillation quality gate, soft quality decay, and lower compaction threshold."""
import time as _time

from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


# ── Quality gate: extractive distiller should skip low-signal chunks ──────

def test_extractive_distiller_skips_low_signal_chunks(tmp_path):
    """Chunks with low extractive signal scores should not be promoted to memories."""
    from memor.distill.distiller import ExtractiveDistiller
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    chunks = [
        # High-signal: contains a decision pattern, enough tokens
        Artifact(id="c1", kind="session_chunk", project="p", source="cc",
                 text="we decided to use argon2 for password hashing instead of bcrypt because it is memory-hard and resistant to GPU attacks in production workloads",
                 token_count=25, created_at=100.0,
                 meta={"session_id": "s1", "role": "assistant", "ord": 0}),
        # Low-signal: filler text, short
        Artifact(id="c2", kind="session_chunk", project="p", source="cc",
                 text="OK let me check that for you now",
                 token_count=8, created_at=101.0,
                 meta={"session_id": "s1", "role": "assistant", "ord": 1}),
        # Low-signal: generic filler
        Artifact(id="c3", kind="session_chunk", project="p", source="cc",
                 text="Sure, I'll do that now and report back",
                 token_count=9, created_at=102.0,
                 meta={"session_id": "s1", "role": "assistant", "ord": 2}),
    ]
    s.add_artifacts(chunks, e.embed([c.text for c in chunks]))
    d = ExtractiveDistiller(s, e)
    mids = d.distill_session("s1", chunks, "p")
    # The decision chunk should be stored; filler chunks should be gated out
    stored = s.db.execute(
        "SELECT id FROM artifacts WHERE kind='memory' AND active=1"
    ).fetchall()
    stored_ids = [r["id"] for r in stored]
    assert len(stored_ids) >= 1  # at least the decision survives
    # Verify no memory was created from the filler texts
    for r in stored:
        art = s.db.execute("SELECT text FROM artifacts WHERE id=?", (r["id"],)).fetchone()
        assert "let me check" not in art["text"].lower()


# ── Soft quality decay for unused memories ────────────────────────────────

def test_decay_halves_quality_for_unused_memories(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    # Insert a memory and give it a quality score
    e = FakeEmbedder(dim=16)
    art = Artifact(id="m1", kind="memory", project="p", source="distill",
                   text="some decision", token_count=5, created_at=100.0,
                   meta={"mem_type": "decision"})
    s.add_artifacts([art], e.embed([art.text]))
    # Set quality: last recalled 20 days ago
    now = _time.time()
    s.db.execute("""
        INSERT INTO memory_quality(artifact_id, recall_count, use_count, last_recalled, quality_score)
        VALUES ('m1', 5, 2, ?, 0.5)
    """, (now - 20 * 86400,))
    s.db.commit()

    decayed = s.decay_quality(stale_days=14, factor=0.5)
    assert decayed == 1
    new_score = s.get_quality_score("m1")
    assert abs(new_score - 0.25) < 0.01  # 0.5 * 0.5 = 0.25


def test_decay_skips_recently_recalled_memories(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    e = FakeEmbedder(dim=16)
    art = Artifact(id="m1", kind="memory", project="p", source="distill",
                   text="some decision", token_count=5, created_at=100.0,
                   meta={"mem_type": "decision"})
    s.add_artifacts([art], e.embed([art.text]))
    now = _time.time()
    s.db.execute("""
        INSERT INTO memory_quality(artifact_id, recall_count, use_count, last_recalled, quality_score)
        VALUES ('m1', 5, 2, ?, 0.5)
    """, (now - 5 * 86400,))  # recalled 5 days ago — still fresh
    s.db.commit()

    decayed = s.decay_quality(stale_days=14, factor=0.5)
    assert decayed == 0
    assert abs(s.get_quality_score("m1") - 0.5) < 0.01


def test_decay_deactivates_after_floor(tmp_path):
    """After enough decay rounds, the quality drops below the floor and
    the memory gets deactivated."""
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    e = FakeEmbedder(dim=16)
    art = Artifact(id="m1", kind="memory", project="p", source="distill",
                   text="some old decision", token_count=5, created_at=100.0,
                   meta={"mem_type": "decision"})
    s.add_artifacts([art], e.embed([art.text]))
    now = _time.time()
    s.db.execute("""
        INSERT INTO memory_quality(artifact_id, recall_count, use_count, last_recalled, quality_score)
        VALUES ('m1', 5, 0, ?, 0.04)
    """, (now - 20 * 86400,))
    s.db.commit()

    decayed = s.decay_quality(stale_days=14, factor=0.5, deactivate_floor=0.03)
    # 0.04 * 0.5 = 0.02 < floor → deactivated
    active = s.db.execute("SELECT active FROM artifacts WHERE id='m1'").fetchone()["active"]
    assert active == 0


# ── Lower compaction threshold ────────────────────────────────────────────

def test_compact_uses_lower_threshold():
    """The compaction threshold should be 0.85, not 0.90."""
    from memor.daemon import COMPACT_SIM_THRESHOLD
    assert COMPACT_SIM_THRESHOLD == 0.85
