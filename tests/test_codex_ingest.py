"""Codex sessions must ingest, and land in the right project.

memor already served recalls to Codex while never reading from it, so work done
in Codex was unrecallable everywhere -- including in Codex. These tests pin the
three things that made the format non-obvious: prose is split across
``response_item`` records while ``event_msg`` duplicates them, tool traffic is
inlined into assistant prose rather than carried structurally, and the cwd is
only available from the ``session_meta`` header.
"""
from __future__ import annotations

import json

from memor.feedback import stamped_turns_from_codex
from memor.ingest.codex import (
    parse_session,
    scan_codex_sessions,
    working_dir_for,
)


def _rollout(tmp_path, name="rollout-2026-05-24T22-35-50-abc", *,
             cwd="/Users/x/Documents/demo-app", records=None, nested=True):
    d = tmp_path / "2026" / "05" / "24" if nested else tmp_path
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.jsonl"
    lines = []
    if cwd is not None:
        lines.append(json.dumps({
            "timestamp": "2026-05-24T17:05:50.439Z",
            "type": "session_meta",
            "payload": {"id": "abc", "cwd": cwd, "originator": "Codex Desktop"},
        }))
    for rec in records or []:
        lines.append(json.dumps(rec))
    path.write_text("\n".join(lines) + "\n")
    return path


def _msg(role, text, ts="2026-05-24T17:06:00.000Z"):
    block = "input_text" if role == "user" else "output_text"
    return {
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "message", "role": role,
                    "content": [{"type": block, "text": text}]},
    }


_LONG_USER = ("Please refactor the billing retry logic so that it backs off "
              "exponentially instead of retrying every second forever.")
# Must clear the shared _signal_score filter, which is what keeps chatter out of
# the store for every agent. "root cause" is the bugfix marker.
_LONG_ASSISTANT = ("The root cause is that the retry loop in billing/client.py had "
                   "no ceiling, so a downstream outage turned into a self-inflicted "
                   "denial of service against our own payment provider.")


def test_parses_prose_from_response_items(tmp_path):
    path = _rollout(tmp_path, records=[
        _msg("user", _LONG_USER),
        _msg("assistant", _LONG_ASSISTANT),
    ])
    arts = parse_session(path, project="demo-app")
    texts = [a.text for a in arts]
    assert _LONG_USER in texts
    assert _LONG_ASSISTANT in texts
    assert all(a.meta["agent"] == "codex" for a in arts)


def test_inlined_tool_blocks_are_not_memories(tmp_path):
    """Codex Desktop writes tool calls as ordinary assistant prose.

    On the local corpus these are 33,932 of 44,772 messages. Storing them would
    fill the store with the machine chatter every other agent's parser drops,
    and would recall a stale command's output as if it were a decision.
    """
    path = _rollout(tmp_path, records=[
        _msg("assistant", "[external_agent_tool_call: Bash]\ncommand: ls -la /tmp/x\n"
                          "[/external_agent_tool_call]"),
        _msg("assistant", "[external_agent_tool_result]\n" + "file.txt\n" * 40
                          + "[/external_agent_tool_result]"),
        _msg("assistant", _LONG_ASSISTANT),
    ])
    texts = [a.text for a in parse_session(path, project="demo-app")]
    assert texts == [_LONG_ASSISTANT]


def test_event_msg_duplicates_are_skipped(tmp_path):
    """event_msg echoes response_item; counting both doubles every artifact."""
    path = _rollout(tmp_path, records=[
        _msg("assistant", _LONG_ASSISTANT),
        {"timestamp": "2026-05-24T17:06:01.000Z", "type": "event_msg",
         "payload": {"type": "agent_message", "message": _LONG_ASSISTANT}},
    ])
    assert len(parse_session(path, project="demo-app")) == 1


def test_developer_role_is_not_user_intent(tmp_path):
    path = _rollout(tmp_path, records=[
        _msg("developer", "You are Codex. Always run the tests before finishing "
                          "and never edit files outside the workspace root."),
        _msg("user", _LONG_USER),
    ])
    assert [a.meta["role"] for a in parse_session(path, project="demo-app")] == ["user"]


def test_working_dir_comes_from_session_meta(tmp_path):
    path = _rollout(tmp_path, cwd="/Users/x/Documents/demo-app")
    assert working_dir_for(path) == "/Users/x/Documents/demo-app"


def test_session_without_cwd_is_not_guessed_into_a_project(tmp_path):
    """The rollout path encodes only a date, so there is nothing to infer from.

    Filing it as "unknown" keeps it out of recall for unrelated projects, which
    is the failure the jcode parser was already written to avoid.
    """
    path = _rollout(tmp_path, cwd=None, records=[_msg("user", _LONG_USER)])
    assert working_dir_for(path) == ""
    sessions = scan_codex_sessions(tmp_path)
    assert [p for _, p, _ in sessions] == ["unknown"]


def test_scan_finds_rollouts_nested_under_date_dirs(tmp_path):
    _rollout(tmp_path, name="rollout-2026-05-24T22-35-50-abc")
    assert len(scan_codex_sessions(tmp_path)) == 1


def test_truncated_final_line_does_not_lose_the_session(tmp_path):
    """A live session's rollout can end mid-write."""
    path = _rollout(tmp_path, records=[_msg("user", _LONG_USER)])
    with path.open("a") as fh:
        fh.write('{"type":"response_item","payload":{"type":"mess')
    assert len(parse_session(path, project="demo-app")) == 1


def test_secrets_are_redacted_on_the_way_in(tmp_path):
    secret = "sk-ant-api03-" + "A" * 60
    path = _rollout(tmp_path, records=[
        _msg("user", f"The deploy fails with this key in the env: {secret} "
                     "and I cannot work out which service rejects it."),
    ])
    arts = parse_session(path, project="demo-app")
    assert arts and secret not in arts[0].text


def test_feedback_grader_reads_the_same_turns(tmp_path):
    """A session memor cannot grade leaves its recalls pending forever."""
    path = _rollout(tmp_path, records=[
        _msg("user", _LONG_USER),
        _msg("assistant", "[external_agent_tool_call: Bash]\ncommand: pytest\n"
                          "[/external_agent_tool_call]"),
        _msg("assistant", _LONG_ASSISTANT),
    ])
    turns = stamped_turns_from_codex(path)
    assert [r for _, r, _ in turns] == ["user", "assistant"]
    assert all(ts > 0 for ts, _, _ in turns)
    assert all(text == text.lower() for _, _, text in turns)


def test_codex_units_reach_the_daemon_registry(tmp_path):
    """The parser is only useful if scan_all_sources actually yields it."""
    from memor.ingest.sources import scan_all_sources

    _rollout(tmp_path, records=[_msg("user", _LONG_USER)])
    units = scan_all_sources(claude_projects_dir=None, codex_sessions_dir=tmp_path)
    assert [u.agent for u in units] == ["codex"]
    assert units[0].parse()
