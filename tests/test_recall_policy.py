"""Tests for strict recall inject profile."""
from __future__ import annotations

from types import SimpleNamespace

from memor.recall_policy import (
    STRICT_SCORE_FLOOR,
    apply_recall_policy,
    active_profile_name,
)


def _hit(score: float, kind: str):
    return SimpleNamespace(score=score, artifact=SimpleNamespace(kind=kind, id=kind + str(score)))


def test_default_profile_passes_hits_through(monkeypatch):
    monkeypatch.delenv("MEMOR_RECALL_PROFILE", raising=False)
    hits = [_hit(0.2, "session_chunk"), _hit(0.3, "memory")]
    out, meta = apply_recall_policy(hits, threshold=0.15)
    assert len(out) == 2
    assert meta["profile"] == "default"
    assert meta["dropped_chunks_for_memories"] == 0


def test_strict_raises_score_floor(monkeypatch):
    monkeypatch.setenv("MEMOR_RECALL_PROFILE", "strict")
    hits = [_hit(0.20, "memory"), _hit(0.30, "memory")]
    out, meta = apply_recall_policy(hits, threshold=0.15)
    assert meta["score_floor_applied"] == STRICT_SCORE_FLOOR
    assert meta["dropped_low_score"] == 1
    assert len(out) == 1 and out[0].score == 0.30


def test_strict_drops_chunks_when_memory_present(monkeypatch):
    monkeypatch.setenv("MEMOR_RECALL_PROFILE", "strict")
    hits = [
        _hit(0.5, "memory"),
        _hit(0.4, "session_chunk"),
        _hit(0.35, "snippet"),
    ]
    out, meta = apply_recall_policy(hits, threshold=0.0)
    assert [h.artifact.kind for h in out] == ["memory"]
    assert meta["dropped_chunks_for_memories"] == 2


def test_strict_keeps_chunks_when_no_memory(monkeypatch):
    monkeypatch.setenv("MEMOR_RECALL_PROFILE", "strict")
    hits = [_hit(0.4, "session_chunk"), _hit(0.35, "session_chunk")]
    out, meta = apply_recall_policy(hits, threshold=0.0)
    assert len(out) == 2
    assert meta["dropped_chunks_for_memories"] == 0


def test_strict_caps_max_hits(monkeypatch):
    monkeypatch.setenv("MEMOR_RECALL_PROFILE", "strict")
    hits = [_hit(0.5 + i * 0.01, "memory") for i in range(6)]
    out, meta = apply_recall_policy(hits, threshold=0.0)
    assert len(out) == 4
    assert meta["trimmed_to_max_hits"] == 2


def test_unknown_profile_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MEMOR_RECALL_PROFILE", "nope")
    assert active_profile_name() == "default"
