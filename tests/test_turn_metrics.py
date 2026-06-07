"""Tests for turn-level ROI measurement — correlating recall events with
tool call patterns to measure actual token savings."""
import json
import time

from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.turn_metrics import parse_turn_metrics, TurnMetric


def _write_transcript(tmp_path, session_id, turns, *, start_ts=1000.0, gap=60.0):
    """Write a transcript with user/assistant turns.
    turns: list of (user_text, tool_names_list)
    gap: seconds between turns (default 60s to ensure clear timestamp separation)
    """
    p = tmp_path / f"{session_id}.jsonl"
    lines = []
    ts = start_ts
    for user_text, tool_names in turns:
        lines.append(json.dumps({
            "type": "user",
            "message": {"content": user_text},
            "timestamp": ts,
            "sessionId": session_id,
        }))
        ts += 1
        content = []
        for name in tool_names:
            content.append({"type": "tool_use", "name": name, "input": {}})
        content.append({"type": "text", "text": "Done."})
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"content": content},
            "timestamp": ts,
            "sessionId": session_id,
        }))
        ts += gap
    p.write_text("\n".join(lines))
    return p


def test_parse_turn_metrics_counts_tool_calls(tmp_path):
    path = _write_transcript(tmp_path, "s1", [
        ("fix the auth bug", ["Read", "Bash", "Edit"]),
        ("now run the tests", ["Bash"]),
    ])
    metrics = parse_turn_metrics(path, "s1")
    assert len(metrics) == 2
    assert metrics[0].tool_call_count == 3
    assert metrics[1].tool_call_count == 1


def test_parse_turn_metrics_records_timestamps(tmp_path):
    path = _write_transcript(tmp_path, "s1", [
        ("hello", ["Read"]),
    ])
    metrics = parse_turn_metrics(path, "s1")
    assert len(metrics) == 1
    assert metrics[0].user_timestamp > 0


def test_turn_metrics_correlates_with_recall(tmp_path):
    """Turns that had recall events should be marked as such."""
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    store = SqliteStore(db_path, dim=16)

    path = _write_transcript(tmp_path, "s1", [
        ("fix the auth bug", ["Read", "Edit"]),
        ("now run the tests", ["Bash", "Bash", "Read", "Bash"]),
    ])
    metrics = parse_turn_metrics(path, "s1")
    # Turn 1 user_timestamp is 1000.0 — insert a recall log at matching time
    store.db.execute(
        "INSERT INTO recall_log(timestamp,project,query_preview,hits_count,"
        "top_score,tokens_injected,latency_ms,status,session_id) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (1000.5, "p", "fix auth bug", 2, 0.8, 100, 5.0, "ok", "s1"))
    store.db.commit()

    from memor.turn_metrics import correlate_with_recalls
    correlated = correlate_with_recalls(metrics, store, "s1")
    assert correlated[0].had_recall is True   # timestamp 1000.0 ≈ 1000.5
    assert correlated[1].had_recall is False   # timestamp 1002.0, no recall nearby


def test_parse_handles_iso8601_timestamps(tmp_path):
    """Real Claude Code transcripts use ISO-8601 strings, not numeric timestamps."""
    p = tmp_path / "s1.jsonl"
    lines = [
        json.dumps({"type": "user", "message": {"content": "hello"},
                    "timestamp": "2026-06-07T10:00:00Z", "sessionId": "s1"}),
        json.dumps({"type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]},
                    "timestamp": "2026-06-07T10:00:05Z", "sessionId": "s1"}),
    ]
    p.write_text("\n".join(lines))
    metrics = parse_turn_metrics(p, "s1")
    assert len(metrics) == 1
    assert metrics[0].user_timestamp > 1_000_000_000  # epoch, not 0
    assert metrics[0].tool_call_count == 1


def test_store_get_tool_call_roi(tmp_path):
    """The ROI metric compares avg tool calls per turn with vs without recall."""
    db_path = str(tmp_path / "m.db")
    store = SqliteStore(db_path, dim=16)

    # Insert turn metrics directly
    store.save_turn_metrics("s1", "p", [
        TurnMetric(turn_idx=0, user_timestamp=100.0, tool_call_count=2, had_recall=True),
        TurnMetric(turn_idx=1, user_timestamp=102.0, tool_call_count=5, had_recall=False),
        TurnMetric(turn_idx=2, user_timestamp=104.0, tool_call_count=1, had_recall=True),
        TurnMetric(turn_idx=3, user_timestamp=106.0, tool_call_count=6, had_recall=False),
    ])

    roi = store.get_token_roi()
    # With recall: avg 1.5 tool calls. Without recall: avg 5.5
    assert roi["avg_tools_with_recall"] < roi["avg_tools_without_recall"]
    assert roi["tool_call_reduction_pct"] > 0
    assert roi["turns_with_recall"] == 2
    assert roi["turns_without_recall"] == 2
