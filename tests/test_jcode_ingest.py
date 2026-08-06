"""jcode sessions must ingest, and land in the right project.

jcode stores a settled ``<id>.json`` per session plus a ``.journal.jsonl``
sidecar for the live one. The two disagree about where the working directory
lives: the JSON's ``working_dir`` is frequently null, while the journal's
``meta.working_dir`` is populated. Getting that wrong files a session under
"unknown" and leaks its memories across project boundaries at recall time.
"""
from __future__ import annotations

import json

from memor.ingest.jcode import (
    parse_session,
    scan_jcode_sessions,
    working_dir_for,
)


def _session(tmp_path, name="session_fox_1_abc", *, working_dir=None, messages=None):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({
        "id": name,
        "working_dir": working_dir,
        "messages": messages if messages is not None else [],
    }))
    return path


def _journal(tmp_path, name, *, working_dir=None, messages=None):
    path = tmp_path / f"{name}.journal.jsonl"
    path.write_text(json.dumps({
        "meta": {"working_dir": working_dir},
        "append_messages": messages or [],
    }) + "\n")
    return path


def _msg(role, text):
    return {"role": role, "content": [{"type": "text", "text": text}]}


def test_parses_text_and_skips_tool_and_reasoning_blocks(tmp_path):
    """Tool payloads and reasoning traces are not memories."""
    path = _session(tmp_path, messages=[
        _msg("user", "please fix the retry loop in the auth client"),
        {"role": "assistant", "content": [
            {"type": "reasoning_trace", "text": "thinking out loud, not a memory"},
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
            {"type": "text", "text": "The retry loop was missing a backoff, so it hammered the API."},
        ]},
    ])
    arts = parse_session(path, "proj", filter_noise=False)
    joined = " ".join(a.text for a in arts)
    assert "retry loop" in joined
    assert "backoff" in joined
    assert "thinking out loud" not in joined


def test_journal_working_dir_wins_over_null_in_session(tmp_path):
    """The real failure: the session JSON says null, the journal knows."""
    name = "session_fox_1_abc"
    _session(tmp_path, name, working_dir=None)
    _journal(tmp_path, name, working_dir="/Users/x/Projects/realproject")
    assert working_dir_for(tmp_path / f"{name}.json").endswith("realproject")


def test_session_working_dir_used_when_no_journal(tmp_path):
    name = "session_fox_2_abc"
    _session(tmp_path, name, working_dir="/Users/x/Projects/fromjson")
    assert working_dir_for(tmp_path / f"{name}.json").endswith("fromjson")


def test_messages_come_from_both_json_and_journal(tmp_path):
    """A live session has settled messages plus un-folded appends."""
    name = "session_fox_3_abc"
    _session(tmp_path, name, working_dir="/tmp/p",
             messages=[_msg("user", "the first question about caching")])
    _journal(tmp_path, name, working_dir="/tmp/p",
             messages=[_msg("assistant", "a later answer about cache invalidation")])
    arts = parse_session(tmp_path / f"{name}.json", "proj", filter_noise=False)
    joined = " ".join(a.text for a in arts)
    assert "first question" in joined
    assert "later answer" in joined


def test_duplicate_text_is_stored_once(tmp_path):
    """The journal repeats what the JSON already holds; that is one memory."""
    name = "session_fox_4_abc"
    dup = "exactly the same sentence in both places"
    _session(tmp_path, name, working_dir="/tmp/p", messages=[_msg("user", dup)])
    _journal(tmp_path, name, working_dir="/tmp/p", messages=[_msg("user", dup)])
    arts = parse_session(tmp_path / f"{name}.json", "proj", filter_noise=False)
    assert len([a for a in arts if a.text == dup]) == 1


def test_artifacts_are_tagged_as_jcode(tmp_path):
    """The dashboard and per-agent metrics key off these fields."""
    path = _session(tmp_path, messages=[_msg("user", "a memory worth keeping here")])
    arts = parse_session(path, "proj", filter_noise=False)
    assert arts
    assert all(a.source == "jcode" for a in arts)
    assert all(a.meta["agent"] == "jcode" for a in arts)
    assert all(a.kind == "session_chunk" for a in arts)


def test_scan_skips_backups_and_journals(tmp_path):
    """A journal is read through its session, never ingested as its own unit."""
    name = "session_fox_5_abc"
    _session(tmp_path, name, working_dir="/tmp/p")
    _journal(tmp_path, name, working_dir="/tmp/p")
    (tmp_path / f"{name}.json.bak").write_text("{}")
    found = scan_jcode_sessions(tmp_path)
    assert [p.name for p, _, _ in found] == [f"{name}.json"]


def test_missing_directory_is_not_an_error(tmp_path):
    assert scan_jcode_sessions(tmp_path / "does-not-exist") == []


def test_corrupt_session_does_not_raise(tmp_path):
    path = tmp_path / "session_bad_1_x.json"
    path.write_text("not json at all")
    assert parse_session(path, "proj") == []
    assert working_dir_for(path) == ""


def test_system_reminders_are_stripped(tmp_path):
    """jcode wraps injected scaffolding in <system-reminder>; it is not a memory."""
    path = _session(tmp_path, messages=[
        _msg("user", "<system-reminder>\n# Session Context\nDate: 2026-08-05\n"
                     "Jcode version: v0.68.0\n</system-reminder>"),
        _msg("user", "the actual question about the retry backoff"),
    ])
    arts = parse_session(path, "proj", filter_noise=False)
    joined = " ".join(a.text for a in arts)
    assert "retry backoff" in joined
    assert "Jcode version" not in joined
    assert "Session Context" not in joined
