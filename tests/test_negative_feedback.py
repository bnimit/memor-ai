"""Tests for negative feedback signals (#16)."""
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


def _make_art(id, project, text, created=100.0):
    return Artifact(id=id, kind="memory", project=project, source="distill",
                    text=text, token_count=len(text.split()), created_at=created,
                    meta={"mem_type": "decision", "session_id": "s1"})


def _write_transcript(tmp_path, session_id, messages):
    """Write a fake JSONL transcript file with the given messages."""
    path = tmp_path / f"{session_id}.jsonl"
    lines = []
    for msg in messages:
        lines.append(json.dumps(msg))
    path.write_text("\n".join(lines))
    return path


# --- Negative signal detection in transcripts ---

def test_detect_user_rejection_signals():
    """Should detect when user explicitly rejects recalled content."""
    from memor.feedback import _detect_negative_signals
    assistant_texts = ["Based on the recalled memory, we should use argon2 for hashing."]
    user_texts = ["no that's wrong, we switched to bcrypt last month"]
    assert _detect_negative_signals(assistant_texts, user_texts)


def test_detect_user_correction_signals():
    """Should detect correction patterns like 'actually' and 'not X'."""
    from memor.feedback import _detect_negative_signals
    assistant_texts = ["The memory says we use postgres for the queue."]
    user_texts = ["actually we moved to redis for the job queue"]
    assert _detect_negative_signals(assistant_texts, user_texts)


def test_no_false_positive_on_normal_conversation():
    """Normal positive conversation should NOT trigger negative signals."""
    from memor.feedback import _detect_negative_signals
    assistant_texts = ["Based on the recalled memory, I'll use argon2 for hashing."]
    user_texts = ["yes that looks good, proceed with the implementation"]
    assert not _detect_negative_signals(assistant_texts, user_texts)


def test_no_false_positive_on_unrelated_no():
    """A 'no' in a different context (not following recall) should not trigger."""
    from memor.feedback import _detect_negative_signals
    assistant_texts = ["Should I also refactor the tests?"]
    user_texts = ["no just the main module for now"]
    assert not _detect_negative_signals(assistant_texts, user_texts)


def test_detect_contradiction_in_assistant():
    """Should detect when assistant contradicts its own recalled content."""
    from memor.feedback import _detect_negative_signals
    assistant_texts = [
        "The recalled memory says we use library X for authentication.",
        "However, looking at the current code, we actually use library Y instead."
    ]
    user_texts = []
    assert _detect_negative_signals(assistant_texts, user_texts)


# --- Store: record_negative and quality impact ---

def test_record_negative_creates_entry(tmp_path):
    """Recording a negative signal should create/update quality entry."""
    s, e, _ = _make_store(tmp_path)
    s.record_recall(["m1"])
    s.record_negative(["m1"])
    row = s.db.execute(
        "SELECT * FROM memory_quality WHERE artifact_id='m1'"
    ).fetchone()
    assert row["negative_count"] == 1


def test_record_negative_increments(tmp_path):
    """Multiple negatives should accumulate."""
    s, e, _ = _make_store(tmp_path)
    s.record_recall(["m1"])
    s.record_negative(["m1"])
    s.record_negative(["m1"])
    row = s.db.execute(
        "SELECT negative_count FROM memory_quality WHERE artifact_id='m1'"
    ).fetchone()
    assert row["negative_count"] == 2


def test_negative_reduces_quality_score(tmp_path):
    """Negative signal should reduce quality score below baseline."""
    s, e, _ = _make_store(tmp_path)
    s.record_recall(["m1"])
    score_before = s.get_quality_score("m1")

    s.record_negative(["m1"])
    score_after = s.get_quality_score("m1")
    assert score_after < score_before


def test_negative_weighs_more_than_positive(tmp_path):
    """A memory with 1 use and 1 negative should score lower than one with just 1 recall."""
    s, e, _ = _make_store(tmp_path)
    s.record_recall(["m1"])
    s.record_usage(["m1"])
    score_with_use = s.get_quality_score("m1")

    s.record_negative(["m1"])
    score_with_negative = s.get_quality_score("m1")
    assert score_with_negative < score_with_use


def test_quality_formula_with_negatives(tmp_path):
    """Verify the Bayesian quality formula accounts for negatives."""
    s, e, _ = _make_store(tmp_path)
    s.record_recall(["m1"])
    s.record_recall(["m1"])
    s.record_recall(["m1"])
    s.record_usage(["m1"])
    s.record_negative(["m1"])

    row = s.db.execute(
        "SELECT recall_count, use_count, negative_count, quality_score "
        "FROM memory_quality WHERE artifact_id='m1'"
    ).fetchone()
    assert row["recall_count"] == 3
    assert row["use_count"] == 1
    assert row["negative_count"] == 1
    # Formula: (use - negative + 1) / (recall + 2) = (1 - 1 + 1) / (3 + 2) = 0.2
    assert abs(row["quality_score"] - 0.2) < 0.01


# --- Integration: analyze_session_feedback with negatives ---

def test_analyze_feedback_detects_negative(tmp_path):
    """Full feedback analysis should detect and record negative signals."""
    from memor.feedback import analyze_session_feedback

    s, e, db_path = _make_store(tmp_path)
    art = _make_art("m1", "proj", "we use postgres for the job queue")
    s.add_artifacts([art], e.embed([art.text]))

    now = time.time()
    s.db.execute("""
        INSERT INTO recall_log(timestamp, project, query_preview, hits_count,
                               top_score, tokens_injected, latency_ms, status, session_id)
        VALUES(?, 'proj', 'job queue', 1, 0.8, 100, 5.0, 'ok', 'sess1')
    """, (now,))
    s.db.commit()
    s.record_recall(["m1"])

    transcript_path = _write_transcript(tmp_path, "sess1", [
        {"type": "assistant", "message": {"content": "Based on the recalled memory, we use postgres for the job queue."}},
        {"type": "human", "message": {"content": "no that's wrong, we switched to redis for the job queue last sprint"}},
        {"type": "assistant", "message": {"content": "I see, let me update that. We now use redis for the job queue."}},
    ])

    result = analyze_session_feedback(s, "sess1", transcript_path, embedder=e)

    row = s.db.execute(
        "SELECT negative_count FROM memory_quality WHERE artifact_id='m1'"
    ).fetchone()
    assert row is not None
    assert row["negative_count"] >= 1


def test_analyze_feedback_no_false_negative(tmp_path):
    """Normal usage should not record negative signals."""
    from memor.feedback import analyze_session_feedback

    s, e, db_path = _make_store(tmp_path)
    art = _make_art("m1", "proj", "we use argon2 for password hashing")
    s.add_artifacts([art], e.embed([art.text]))

    now = time.time()
    s.db.execute("""
        INSERT INTO recall_log(timestamp, project, query_preview, hits_count,
                               top_score, tokens_injected, latency_ms, status, session_id)
        VALUES(?, 'proj', 'password hashing', 1, 0.8, 100, 5.0, 'ok', 'sess1')
    """, (now,))
    s.db.commit()
    s.record_recall(["m1"])

    transcript_path = _write_transcript(tmp_path, "sess1", [
        {"type": "assistant", "message": {"content": "Using argon2 for password hashing as previously decided."}},
        {"type": "human", "message": {"content": "yes perfect, that looks good"}},
    ])

    analyze_session_feedback(s, "sess1", transcript_path, embedder=e)

    row = s.db.execute(
        "SELECT negative_count FROM memory_quality WHERE artifact_id='m1'"
    ).fetchone()
    neg = row["negative_count"] if row else 0
    assert neg == 0
