"""Feedback must reach every agent, not only Claude.

memor's purpose is one memory store shared across a machine's coding agents.
The quality loop that decides whether a served memory actually helped ran for
Claude alone, so the cross-tool recalls -- the product's whole point -- were
served and then never graded.

The blocker was not the guard clause in the daemon. It was the signature:
``analyze_session_feedback`` asked for a ``transcript_path``, and Goose keeps no
transcript file, only rows in SQLite. These tests pin the shape that fixes it:
each source yields ``(timestamp, role, text)`` and the analyzer stops caring
where the text came from.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from memor.embed.fake import FakeEmbedder
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact

DECISION = (
    "We moved the payment retry loop off the request path because the "
    "synchronous retry held a database row lock for up to 30 seconds and "
    "serialised every merchant behind one slow charge."
)
QUERY = "why did we move payment retries off the request path"


def test_claude_transcripts_yield_stamped_turns(tmp_path):
    """The reader that already worked, expressed in the new shape."""
    from memor.feedback import stamped_turns_from_claude

    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(rec) for rec in [
        {"type": "assistant", "timestamp": "2026-08-22T15:00:00Z",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": DECISION}]}},
        {"type": "user", "timestamp": "2026-08-22T15:01:00Z",
         "message": {"role": "user", "content": QUERY}},
    ]))

    turns = stamped_turns_from_claude(path)

    assert [role for _, role, _ in turns] == ["assistant", "user"]
    assert turns[0][0] > 0 and turns[1][0] > turns[0][0]
    assert DECISION.lower() in turns[0][2]


def test_jcode_journals_yield_stamped_turns(tmp_path):
    """The gap this work exists to close.

    jcode writes ``{append_messages, meta}`` records, which the Claude reader
    returns nothing for. The reading logic already lives in
    ``memor.ingest.jcode``; this exposes it to the feedback path.
    """
    from memor.feedback import stamped_turns_from_jcode

    session = tmp_path / "session_x"
    session.with_suffix(".json").write_text(json.dumps({"messages": [
        {"role": "assistant", "timestamp": "2026-08-22T15:00:00Z",
         "content": [{"type": "text", "text": DECISION}]},
        {"role": "user", "timestamp": "2026-08-22T15:01:00Z",
         "content": QUERY},
    ]}))

    turns = stamped_turns_from_jcode(session.with_suffix(".json"))

    assert [role for _, role, _ in turns] == ["assistant", "user"]
    assert turns[0][0] > 0
    assert DECISION.lower() in turns[0][2]


def test_goose_sessions_yield_stamped_turns(tmp_path):
    """Goose has no transcript file at all -- this is why the signature changed.

    Its history is rows in SQLite, so a reader keyed on ``Path`` cannot express
    it. The fixture mirrors the real schema read by ``memor.ingest.goose``.
    """
    from memor.feedback import stamped_turns_from_goose

    db = tmp_path / "sessions.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, "
        "message_id TEXT, role TEXT, content_json TEXT, created_timestamp INTEGER)"
    )
    con.executemany(
        "INSERT INTO messages (session_id, message_id, role, content_json, "
        "created_timestamp) VALUES (?,?,?,?,?)",
        [
            ("s1", "m1", "assistant",
             json.dumps([{"type": "text", "text": DECISION}]), 1787000000),
            ("s1", "m2", "user",
             json.dumps([{"type": "text", "text": QUERY}]), 1787000060),
        ],
    )
    con.commit()
    con.close()

    turns = stamped_turns_from_goose(db, "s1")

    assert [role for _, role, _ in turns] == ["assistant", "user"]
    assert turns[0][0] == 1787000000
    assert DECISION.lower() in turns[0][2]


def test_kimi_wire_yields_stamped_turns(tmp_path):
    """Kimi records a protocol stream, not a conversation.

    A user turn is a ``TurnBegin`` envelope; an assistant reply arrives as a
    run of ``ContentPart`` fragments. Those are joined, because the n-gram
    check needs a whole reply -- scored fragment by fragment, a memory quoted
    across a sentence boundary would never register.
    """
    from memor.feedback import stamped_turns_from_kimi

    wire = tmp_path / "wire.jsonl"
    wire.write_text("\n".join(json.dumps(rec) for rec in [
        {"protocol_version": 1, "type": "hello"},
        {"timestamp": "2026-08-22T15:00:00Z",
         "message": {"type": "TurnBegin", "payload": {"user_input": QUERY}}},
        {"timestamp": "2026-08-22T15:00:10Z",
         "message": {"type": "ContentPart",
                     "payload": {"type": "text", "text": DECISION[:60]}}},
        {"timestamp": "2026-08-22T15:00:11Z",
         "message": {"type": "ContentPart",
                     "payload": {"type": "text", "text": DECISION[60:]}}},
    ]))

    turns = stamped_turns_from_kimi(wire)

    assert [role for _, role, _ in turns] == ["user", "assistant"]
    assert DECISION.lower() in turns[1][2], "assistant fragments were not joined"


def test_analyzer_accepts_turns_from_any_source(tmp_path):
    """The point of the refactor: grading no longer depends on the agent.

    Given a goose recall and turns written after it, the verdict settles to
    ``used`` -- the same outcome Claude already got, reached without a
    transcript file existing anywhere.
    """
    from memor.feedback import analyze_session_feedback

    store, _db = _store_with_memory(tmp_path)
    recall_id = store.log_recall(
        project="acc", query_preview=QUERY, hits_count=1, top_score=0.9,
        tokens_injected=40, latency_ms=5.0, status="ok",
        session_id="sess-1", agent="goose",
    )
    store.record_recall_candidates(recall_id, ["jc-1"])
    assert _outcomes(store) == [("jc-1", "pending")]

    later = time.time() + 30
    turns = [(later, "assistant", ("right. " + DECISION + " keeping it async").lower())]

    assert analyze_session_feedback(store, "sess-1", turns=turns) == 1
    assert _outcomes(store) == [("jc-1", "used")]


def test_turns_before_the_recall_are_not_evidence(tmp_path):
    """Causality is what makes the credit meaningful.

    Text written before a memory was served cannot have been influenced by it.
    Pinned because the refactor moves the timestamps through a new path, and
    losing this check would silently credit every memory in a session.
    """
    from memor.feedback import analyze_session_feedback

    store, _db = _store_with_memory(tmp_path)
    recall_id = store.log_recall(
        project="acc", query_preview=QUERY, hits_count=1, top_score=0.9,
        tokens_injected=40, latency_ms=5.0, status="ok",
        session_id="sess-2", agent="jcode",
    )
    store.record_recall_candidates(recall_id, ["jc-1"])

    earlier = time.time() - 600
    turns = [(earlier, "assistant", ("right. " + DECISION).lower())]

    analyze_session_feedback(store, "sess-2", turns=turns)
    assert _outcomes(store) == [("jc-1", "unused")]


def test_transcript_path_still_works(tmp_path):
    """Callers passing a Claude transcript keep working.

    The daemon, the CLI and existing tests all call with a path. Changing the
    signature without keeping this would trade one broken agent for several.
    """
    from memor.feedback import analyze_session_feedback

    store, _db = _store_with_memory(tmp_path)
    recall_id = store.log_recall(
        project="acc", query_preview=QUERY, hits_count=1, top_score=0.9,
        tokens_injected=40, latency_ms=5.0, status="ok",
        session_id="sess-3", agent="claude",
    )
    store.record_recall_candidates(recall_id, ["jc-1"])

    from datetime import datetime, timedelta, timezone
    later = (datetime.now(timezone.utc) + timedelta(seconds=30))
    transcript = tmp_path / "sess-3.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant",
        "timestamp": later.isoformat().replace("+00:00", "Z"),
        "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Right. " + DECISION + " Keeping it async."}]},
    }))

    assert analyze_session_feedback(store, "sess-3", transcript) == 1
    assert _outcomes(store) == [("jc-1", "used")]


def test_cross_tool_stats_separate_readers_from_writers(tmp_path):
    """The number the dashboard exists to show.

    A recall is cross-tool when the reading agent is not the tool that wrote
    the artifact. ``distill`` is the shared distiller rather than a rival
    harness, so memories it produced count as every agent's own -- getting this
    wrong inflated the figure from 37 to 44 during an audit of the review.
    """
    from memor.store.sqlite_store import SqliteStore  # noqa: F401
    from memor.crosstool import cross_tool_stats

    store, _db = _store_with_memory(tmp_path)
    store.add_artifacts(
        [Artifact(id="cc-1", kind="memory", project="acc", source="claude_code",
                  text="unrelated", token_count=5, created_at=time.time(), meta={})],
        [FakeEmbedder(dim=64).embed(["unrelated"])[0]],
    )
    _settle(store, agent="claude", artifact="jc-1", outcome="used")     # cross
    _settle(store, agent="claude", artifact="cc-1", outcome="used")     # same
    _settle(store, agent="claude", artifact="cc-1", outcome="unused")   # same

    stats = cross_tool_stats(store)

    # `judged` counts settled verdicts only. An `unused` recall means the agent
    # wrote nothing afterwards, which says as much about the session ending as
    # about the memory, so it is reported but not held against the use rate.
    assert stats["cross_tool"]["used"] == 1
    assert stats["cross_tool"]["judged"] == 1
    assert stats["same_tool"]["used"] == 1
    assert stats["same_tool"]["unused"] == 1
    assert stats["same_tool"]["judged"] == 1
    assert stats["cross_tool"]["use_rate"] == 100.0
    assert stats["same_tool"]["use_rate"] == 100.0

    pairs = {(p["reader"], p["writer"]): p for p in stats["pairs"]}
    assert pairs[("claude", "jcode")]["cross_tool"] is True
    assert pairs[("claude", "claude_code")]["cross_tool"] is False


def test_distilled_memories_are_not_cross_tool(tmp_path):
    """``distill`` is memor's own distiller, not another agent."""
    from memor.crosstool import cross_tool_stats

    store, _db = _store_with_memory(tmp_path)
    store.add_artifacts(
        [Artifact(id="d-1", kind="memory", project="acc", source="distill",
                  text="distilled insight", token_count=5,
                  created_at=time.time(), meta={})],
        [FakeEmbedder(dim=64).embed(["distilled insight"])[0]],
    )
    _settle(store, agent="jcode", artifact="d-1", outcome="used")

    stats = cross_tool_stats(store)

    assert stats["cross_tool"]["judged"] == 0, (
        "distilled output counted as another tool's memory"
    )
    assert stats["same_tool"]["used"] == 1


def _store_with_memory(tmp_path) -> tuple[SqliteStore, str]:
    embedder = FakeEmbedder(dim=64)
    db = str(tmp_path / "memor.db")
    store = SqliteStore(db, dim=embedder.dim)
    store.add_artifacts(
        [Artifact(id="jc-1", kind="memory", project="acc", source="jcode",
                  text=DECISION, token_count=40, created_at=time.time(), meta={})],
        [embedder.embed([DECISION])[0]],
    )
    return store, db


def _settle(store: SqliteStore, *, agent: str, artifact: str, outcome: str) -> None:
    recall_id = store.log_recall(
        project="acc", query_preview=QUERY, hits_count=1, top_score=0.9,
        tokens_injected=40, latency_ms=5.0, status="ok",
        session_id=f"s-{agent}-{artifact}-{outcome}", agent=agent,
    )
    store.record_recall_candidates(recall_id, [artifact])
    store.db.execute(
        "UPDATE recall_outcomes SET outcome=? WHERE recall_id=? AND artifact_id=?",
        (outcome, recall_id, artifact),
    )
    store.db.commit()


def _outcomes(store: SqliteStore) -> list[tuple[str, str]]:
    return [(r["artifact_id"], r["outcome"]) for r in
            store.db.execute("SELECT artifact_id, outcome FROM recall_outcomes")]


def test_dashboard_exposes_cross_tool(tmp_path, monkeypatch):
    """The panel's data must reach the browser, not just the module.

    Exercised through the HTTP route rather than by calling
    ``cross_tool_stats`` again, because the failure this guards against is a
    wiring one: an endpoint that raises, or returns a shape the page cannot
    render, looks identical to an empty store from the UI.
    """
    from fastapi.testclient import TestClient
    from memor.dashboard.server import create_app

    store, db = _store_with_memory(tmp_path)
    _settle(store, agent="claude", artifact="jc-1", outcome="used")

    client = TestClient(create_app(db_path=db))
    response = client.get("/api/cross-tool")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cross_tool"]["used"] == 1
    assert payload["cross_tool"]["use_rate"] == 100.0
    assert any(p["cross_tool"] for p in payload["pairs"])
