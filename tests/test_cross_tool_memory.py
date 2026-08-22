"""Acceptance tests for the cross-tool memory layer.

memor's purpose is one shared store for every LLM tool on a machine: a decision
recorded while using one agent should be recallable from another. That property
had no test. It was verified only by joining production tables, which cannot
tell a working feature from a lucky arrangement of rows, and cannot fail a build
when an integration breaks.

These tests pin the two halves separately, because an architecture review found
they are in different states: retrieval works across tools today, while the
quality feedback loop reaches only Claude.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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


@pytest.fixture
def store_with_jcode_memory(tmp_path):
    """A store holding one memory written while the user was in jcode."""
    embedder = FakeEmbedder(dim=64)
    db = str(tmp_path / "memor.db")
    store = SqliteStore(db, dim=embedder.dim)
    store.add_artifacts(
        [Artifact(id="jc-1", kind="memory", project="acc", source="jcode",
                  text=DECISION, token_count=40, created_at=time.time(), meta={})],
        [embedder.embed([DECISION])[0]],
    )
    return store, db, embedder


@pytest.mark.parametrize("reader", ["goose", "jcode", "claude"])
def test_memory_written_by_one_tool_is_delivered_to_another(
    store_with_jcode_memory, tmp_path, reader
):
    """The product thesis, as an assertion.

    Driven through ``hook_server.handle_request`` rather than ``recall()``
    because the hook is the public interface an agent actually reaches, and it
    is where the agent identity is resolved. ``_memor_agent`` is the key the
    installer stamps into each agent's hook command.
    """
    import memor.hook_server as hook_server

    store, db, embedder = store_with_jcode_memory
    project = tmp_path / "acc"
    project.mkdir(exist_ok=True)

    response = hook_server.handle_request(
        {"prompt": QUERY, "cwd": str(project), "session_id": f"s-{reader}",
         "_memor_agent": reader, "hook_event_name": "UserPromptSubmit"},
        db_path=db, embedder=embedder,
    )

    assert "payment retry loop" in json.dumps(response), (
        f"{reader} did not receive the jcode-written memory"
    )
    logged = store.db.execute(
        "SELECT agent FROM recall_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert logged["agent"] == reader


def test_feedback_analyzer_is_not_claude_specific(store_with_jcode_memory, tmp_path):
    """The scoring logic works for any agent; only its caller is gated.

    Worth pinning because it is the difference between "extend the feedback
    loop past Claude" being a wiring change and a rewrite. Given a goose recall
    and a transcript where the assistant reuses the memory, the analyzer
    resolves the verdict and credits the artifact.
    """
    from memor.feedback import analyze_session_feedback

    store, _db, _embedder = store_with_jcode_memory
    recall_id = store.log_recall(
        project="acc", query_preview=QUERY, hits_count=1, top_score=0.9,
        tokens_injected=40, latency_ms=5.0, status="ok",
        session_id="sess-1", agent="goose",
    )
    store.record_recall_candidates(recall_id, ["jc-1"])
    assert _outcomes(store) == [("jc-1", "pending")]

    transcript = tmp_path / "sess-1.jsonl"
    # The timestamp must be *after* the recall: the analyzer only counts text
    # written subsequently as evidence of use, which is what makes the credit
    # causal rather than coincidental.
    written_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    transcript.write_text(json.dumps({
        "type": "assistant",
        "timestamp": written_at.isoformat().replace("+00:00", "Z"),
        "message": {"role": "assistant", "content": [{"type": "text", "text":
            "Right. " + DECISION + " So we keep it async."}]},
    }))

    assert analyze_session_feedback(store, "sess-1", transcript) == 1
    assert _outcomes(store) == [("jc-1", "used")]


def test_feedback_cannot_read_a_jcode_transcript():
    """Documents the specific gap, so closing it flips this test.

    ``_extract_stamped_texts`` is the reader the live path uses
    (``feedback.py:245``), and it understands Claude's ``{type, message}``
    records with ISO timestamps. jcode writes ``{append_messages, meta}``, so it
    yields nothing, and every jcode recall stays unadjudicated.

    The reader for that shape already exists in ``memor/ingest/jcode.py`` -- it
    is simply not reachable from the feedback path.

    When feedback gains a per-agent reader, invert this assertion.
    """
    from memor.feedback import _extract_stamped_texts

    assert _extract_stamped_texts(_jcode_journal()) == ([], []), (
        "feedback can now read jcode transcripts -- update this test and "
        "remove the Claude-only guard in memor/daemon.py"
    )


def test_feedback_reads_claude_transcripts(tmp_path):
    """The control for the test above.

    Without this, a reader that returned nothing for *every* agent would still
    pass, and the jcode assertion would prove nothing about jcode.
    """
    from memor.feedback import _extract_stamped_texts

    transcript = tmp_path / "c.jsonl"
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    transcript.write_text("\n".join(json.dumps(rec) for rec in [
        {"type": "assistant", "timestamp": stamp,
         "message": {"role": "assistant", "content": [{"type": "text", "text": DECISION}]}},
        {"type": "user", "timestamp": stamp,
         "message": {"role": "user", "content": QUERY}},
    ]))

    assistant, user = _extract_stamped_texts(transcript)
    assert assistant and user, "the Claude reader regressed"


@pytest.mark.parametrize("mode,document", [
    ("top-level key renamed", {"turns": [{"role": "assistant", "content": DECISION}]}),
    ("role values renamed", {"messages": [{"role": "model", "content": DECISION}]}),
    ("content key renamed", {"messages": [{"role": "assistant", "body": DECISION}]}),
    ("content becomes blocks",
     {"messages": [{"role": "assistant", "content": [{"kind": "text", "value": DECISION}]}]}),
    ("messages become a dict",
     {"messages": {"0": {"role": "assistant", "content": DECISION}}}),
])
def test_a_harness_format_change_is_silent(tmp_path, mode, document):
    """Passive capture is the moat; this is the hole in it.

    memor ingests transcripts harnesses write for their own reasons, so it is
    exposed to formats it does not control. Every plausible shape of change
    produces zero artifacts with no exception and no warning, so the flow stops
    and nothing says so. The failure surfaces months later as "memory got
    worse", with no way to date it.

    Parametrised over five distinct drift modes rather than one, because a
    single case would not show that *nothing* in the parser raises. If drift
    detection is added, assert on the signal instead of the silence.
    """
    from memor.ingest.jcode import parse_session

    drifted = tmp_path / f"{mode.replace(' ', '_')}.json"
    drifted.write_text(json.dumps(document))

    assert parse_session(drifted, project="drift") == [], (
        f"{mode}: parser now recovers or raises -- if detection was added, "
        "assert on the warning rather than the empty result"
    )


def test_the_current_format_still_parses(tmp_path):
    """Control for the drift tests: proves they fail for the right reason.

    Asserts one artifact, not two. The user turn is kept because any user
    message over six tokens is signal; the assistant reply is dropped because
    at 35 tokens it clears neither the 100-token bar nor the decision regex in
    ``_signal_score``. That is the noise filter working, and pinning the real
    number here is what makes the drift assertions above mean "the parser saw
    nothing" rather than "the filter ate everything".
    """
    from memor.ingest.jcode import parse_session

    current = tmp_path / "session_a.json"
    current.write_text(json.dumps({"messages": [
        {"role": "user", "content": QUERY},
        {"role": "assistant", "content": DECISION},
    ]}))

    artifacts = parse_session(current, project="drift")
    assert [a.meta["role"] for a in artifacts] == ["user"]


def _outcomes(store) -> list[tuple[str, str]]:
    return [(r["artifact_id"], r["outcome"]) for r in
            store.db.execute("SELECT artifact_id, outcome FROM recall_outcomes")]


def _jcode_journal() -> Path:
    """A real jcode journal, or a faithful synthetic one when none is present."""
    import glob
    import os
    import tempfile

    live = sorted(glob.glob(os.path.expanduser("~/.jcode/sessions/*.journal.jsonl")))
    if live:
        return Path(live[0])
    path = Path(tempfile.mkdtemp()) / "session_x.journal.jsonl"
    path.write_text(json.dumps({
        "meta": {"updated_at": "2026-08-22T15:00:00Z"},
        "append_messages": [{"role": "assistant", "content": DECISION}],
    }))
    return path


def test_distill_is_not_a_rival_tool():
    """Guards the definition the cross-tool measurement depends on.

    ``distill`` is the distiller writing back into the store, not another
    harness. Counting a recall as cross-tool merely because its writer differs
    from its reader inflates the figure -- it reported 44 against the correct
    37 during a re-audit of this review, the difference being seven rows of
    jcode reading distilled output.

    Pinned as an assertion about ownership rather than a count, because counts
    move whenever the daemon ingests.
    """
    from memor.ingest.sources import default_local_source_paths

    harnesses = set(default_local_source_paths())
    assert not any("distill" in name for name in harnesses), (
        "distill is now an ingest source -- revisit whether it counts as a "
        "distinct tool in the cross-tool measurement"
    )


def test_mcp_exposes_recall_but_not_write():
    """The read-only MCP surface is the architectural claim of the review.

    Competitors expose ``add_memories`` and depend on the model choosing to
    call it. memor's write path is passive ingestion, so its MCP surface offers
    retrieval only. If a write tool is ever added, the differentiator in the
    review's strategy section changes and should be re-argued.
    """
    from memor.proxy.mcp_retrieve import handle_tools_list

    names = {tool["name"] for tool in handle_tools_list()["tools"]}
    assert names == {"memor_recall", "memor_retrieve"}, (
        f"MCP surface changed: {names}. If a write tool was added, memor now "
        "shares the opt-in capture model it is differentiated against."
    )


@pytest.mark.skipif(
    not Path("~/.local/share/goose/sessions/sessions.db").expanduser().exists(),
    reason="goose is not installed on this machine",
)
def test_goose_is_ingested_without_cooperating():
    """The sharpest evidence for the passive-capture claim.

    Competitors need the model to call a save tool, or a hook the vendor wrote
    per harness. memor reads Goose's own SQLite session store, so Goose
    contributes memory while having no idea memor exists: it is absent from
    Goose's config, and Goose has never issued a recall.

    Skipped rather than mocked when Goose is missing, because a mocked SQLite
    file would prove the parser works, not that the integration does -- and the
    integration is the whole claim.
    """
    from memor.ingest.goose import GOOSE_DB_PATH, scan_goose_sessions

    assert scan_goose_sessions(GOOSE_DB_PATH), (
        "no goose sessions discoverable -- if the schema changed, this is the "
        "silent-drift failure mode that P4 exists to detect"
    )
