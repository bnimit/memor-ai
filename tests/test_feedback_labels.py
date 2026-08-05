"""Verdicts on recalled memories, and the four defects that made them fiction.

The old analyzer credited one artifact with 2,180 uses and 1,770 rejections
against 40 recalls. Four faults compounded:

1. usage was attributed by time window over ``last_recalled``, a single mutable
   column, so a long session credited every artifact in the project;
2. one rejection phrase anywhere in a session penalised all of them;
3. user turns were matched on ``type == "human"``, which no transcript emits,
   so the rejection channel read assistant prose alone;
4. counters were incremented, so re-analysing inflated them again.

Each has a test here. The invariant they exist to protect — a memory cannot be
used more often than it was served — is now guaranteed by deriving the counters
from the ledger rather than incrementing them.
"""
from __future__ import annotations

import json
import time

import pytest

from memor.embed.fake import FakeEmbedder
from memor.feedback import analyze_session_feedback
from memor.store.sqlite_store import NEUTRAL_QUALITY, SqliteStore
from memor.types import Artifact


@pytest.fixture
def store(tmp_path):
    return SqliteStore(str(tmp_path / "m.db"), dim=16)


@pytest.fixture
def embedder():
    return FakeEmbedder(dim=16)


def _art(store, embedder, aid: str, text: str, project: str = "proj"):
    a = Artifact(id=aid, kind="memory", project=project, source="distill",
                 text=text, token_count=10, created_at=100.0, meta={})
    store.add_artifacts([a], embedder.embed([a.text]))
    return a


def _recall(store, aids: list[str], *, session="s1", ts=None, project="proj"):
    rid = store.log_recall(project=project, query_preview="q", hits_count=len(aids),
                           top_score=0.8, tokens_injected=50, latency_ms=5.0,
                           status="ok", session_id=session)
    if ts is not None:
        store.db.execute("UPDATE recall_log SET timestamp=? WHERE id=?", (ts, rid))
        store.db.commit()
    store.record_recall_candidates(rid, aids)
    return rid


def _transcript(tmp_path, name, records):
    p = tmp_path / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records))
    return p


def _counts(store, aid):
    r = store.db.execute(
        "SELECT recall_count, use_count, negative_count, quality_score "
        "FROM memory_quality WHERE artifact_id=?", (aid,)).fetchone()
    return dict(r) if r else None


# --- defect 1: only what was served is judged --------------------------------


def test_only_the_memories_actually_served_are_judged(store, embedder, tmp_path):
    """The window query used to sweep in every artifact in the project."""
    _art(store, embedder, "served", "we use argon2 for password hashing")
    _art(store, embedder, "bystander", "the queue runs on postgres")
    _recall(store, ["served"], ts=1000.0)

    t = _transcript(tmp_path, "s1", [
        {"type": "assistant", "timestamp": 1001.0,
         "message": {"content": "we use argon2 for password hashing here"}},
    ])
    analyze_session_feedback(store, "s1", t, embedder=embedder)

    assert _counts(store, "served")["use_count"] == 1
    assert _counts(store, "bystander") is None, "an unserved artifact was judged"


def test_evidence_written_before_the_recall_does_not_count(store, embedder, tmp_path):
    """A memory cannot have been used by text that predates it."""
    _art(store, embedder, "m1", "we use argon2 for password hashing")
    _recall(store, ["m1"], ts=5000.0)

    t = _transcript(tmp_path, "s1", [
        {"type": "assistant", "timestamp": 1000.0,
         "message": {"content": "we use argon2 for password hashing here"}},
    ])
    analyze_session_feedback(store, "s1", t, embedder=embedder)
    assert _counts(store, "m1")["use_count"] == 0


# --- defect 2: rejection is attributed, not broadcast ------------------------


def test_a_rejection_does_not_penalise_every_memory_recalled(store, embedder, tmp_path):
    _art(store, embedder, "wrong", "the job queue runs on postgres")
    _art(store, embedder, "fine", "the api uses cursor pagination")
    _recall(store, ["wrong"], ts=1000.0)
    _recall(store, ["fine"], ts=3000.0)

    t = _transcript(tmp_path, "s1", [
        {"type": "user", "timestamp": 1500.0,
         "message": {"content": "no that's wrong, we switched to redis"}},
    ])
    analyze_session_feedback(store, "s1", t, embedder=embedder)

    assert _counts(store, "wrong")["negative_count"] == 1
    assert _counts(store, "fine")["negative_count"] == 0, "blanket penalty is back"


def test_rejection_outranks_use(store, embedder, tmp_path):
    """Acted on, then corrected: harmful, however faithfully it was used."""
    _art(store, embedder, "m1", "the job queue runs on postgres")
    _recall(store, ["m1"], ts=1000.0)
    t = _transcript(tmp_path, "s1", [
        {"type": "assistant", "timestamp": 1001.0,
         "message": {"content": "the job queue runs on postgres, so I will use that"}},
        {"type": "user", "timestamp": 1002.0,
         "message": {"content": "no that's wrong, we switched to redis"}},
    ])
    analyze_session_feedback(store, "s1", t, embedder=embedder)
    counts = _counts(store, "m1")
    assert counts["negative_count"] == 1 and counts["use_count"] == 0


# --- defect 3: user turns are "user", never "human" --------------------------


def test_user_rejections_are_read_from_user_records(store, embedder, tmp_path):
    """The old code tested type == "human", so this channel never fired."""
    _art(store, embedder, "m1", "the job queue runs on postgres")
    _recall(store, ["m1"], ts=1000.0)
    t = _transcript(tmp_path, "s1", [
        {"type": "user", "timestamp": 1100.0,
         "message": {"content": "no that's wrong, we moved to redis"}},
    ])
    analyze_session_feedback(store, "s1", t, embedder=embedder)
    assert _counts(store, "m1")["negative_count"] == 1


# --- defect 4: settling a verdict twice is impossible ------------------------


def test_reanalysis_cannot_inflate_the_counts(store, embedder, tmp_path):
    _art(store, embedder, "m1", "we use argon2 for password hashing")
    _recall(store, ["m1"], ts=1000.0)
    t = _transcript(tmp_path, "s1", [
        {"type": "assistant", "timestamp": 1001.0,
         "message": {"content": "we use argon2 for password hashing here"}},
    ])
    for _ in range(5):
        analyze_session_feedback(store, "s1", t, embedder=embedder)
    assert _counts(store, "m1")["use_count"] == 1


# --- the invariant, which is what all of this is for -------------------------


def test_uses_can_never_exceed_the_times_a_memory_was_served(store, embedder, tmp_path):
    _art(store, embedder, "m1", "we use argon2 for password hashing")
    for i in range(3):
        _recall(store, ["m1"], ts=1000.0 + i)
    t = _transcript(tmp_path, "s1", [
        {"type": "assistant", "timestamp": 2000.0 + i,
         "message": {"content": "we use argon2 for password hashing here"}}
        for i in range(50)
    ])
    analyze_session_feedback(store, "s1", t, embedder=embedder)

    c = _counts(store, "m1")
    assert c["use_count"] <= c["recall_count"] == 3
    assert 0.0 <= c["quality_score"] <= 1.0


def test_an_unjudged_memory_keeps_the_prior_rather_than_a_penalty(store, embedder):
    """Being retrieved before anyone looked at it is not evidence of anything."""
    _art(store, embedder, "m1", "we use argon2 for password hashing")
    for i in range(40):
        _recall(store, ["m1"], ts=1000.0 + i)
    store.recompute_quality_from_outcomes(["m1"])
    assert _counts(store, "m1")["quality_score"] == NEUTRAL_QUALITY


def test_an_unused_hit_is_recorded_as_a_clean_negative(store, embedder, tmp_path):
    """The agent saw it and did not use it — that is a label, not a gap.

    No embedder: FakeEmbedder's vectors are arbitrary, so the semantic channel
    would decide this at random rather than on the text.
    """
    _art(store, embedder, "m1", "the queue runs on postgres")
    _recall(store, ["m1"], ts=1000.0)
    t = _transcript(tmp_path, "s1", [
        {"type": "assistant", "timestamp": 1001.0,
         "message": {"content": "let me look at the css grid layout instead"}},
    ])
    analyze_session_feedback(store, "s1", t, embedder=None)

    row = store.db.execute(
        "SELECT outcome FROM recall_outcomes WHERE artifact_id='m1'").fetchone()
    assert row["outcome"] == "unused"
    assert _counts(store, "m1")["use_count"] == 0


# --- both delivery paths -----------------------------------------------------


def test_a_proxy_served_recall_is_judged_via_the_conversation_key(store, embedder, tmp_path):
    """Proxy rows carry no session id — the key is the only way to find them."""
    _art(store, embedder, "m1", "we use argon2 for password hashing")
    rid = store.log_recall(project="proj", query_preview="q", hits_count=1,
                           top_score=0.8, tokens_injected=50, latency_ms=5.0,
                           status="ok", session_id="", agent="claude",
                           conversation_key="abc123")
    store.db.execute("UPDATE recall_log SET timestamp=1000.0 WHERE id=?", (rid,))
    store.db.commit()
    store.record_recall_candidates(rid, ["m1"])

    t = _transcript(tmp_path, "s1", [
        {"type": "assistant", "timestamp": 1001.0,
         "message": {"content": "we use argon2 for password hashing here"}},
    ])
    analyze_session_feedback(store, "s1", t, embedder=embedder,
                             conversation_key="abc123")
    assert _counts(store, "m1")["use_count"] == 1


# --- repairing what is already on disk ---------------------------------------


def test_reopening_clears_impossible_counts(tmp_path):
    path = str(tmp_path / "legacy.db")
    first = SqliteStore(path, dim=16)
    first.db.execute(
        "INSERT INTO memory_quality(artifact_id, recall_count, use_count, "
        "negative_count, quality_score) VALUES('a', 40, 2180, 1770, 9.79)")
    first.db.execute(
        "INSERT INTO memory_quality(artifact_id, recall_count, use_count, "
        "negative_count, quality_score) VALUES('b', 10, 4, 1, 0.417)")
    first.db.commit()
    first.db.close()

    reopened = SqliteStore(path, dim=16)
    bad = reopened.db.execute(
        "SELECT * FROM memory_quality WHERE artifact_id='a'").fetchone()
    assert bad["use_count"] == 0 and bad["negative_count"] == 0
    assert bad["recall_count"] == 40, "the sound column must survive"
    assert bad["quality_score"] == NEUTRAL_QUALITY

    ok = reopened.db.execute(
        "SELECT * FROM memory_quality WHERE artifact_id='b'").fetchone()
    assert ok["use_count"] == 4, "a consistent row must be left alone"
