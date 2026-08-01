"""Tests for Kimi wire.jsonl ingest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from memor.ingest.kimi import (
    load_work_dir_map,
    parse_wire,
    resolve_kimi_project,
    scan_kimi_sessions,
)


def _write_wire(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_parse_wire_keeps_user_and_assistant_text(tmp_path):
    wire = tmp_path / "sess" / "wire.jsonl"
    _write_wire(wire, [
        {"type": "metadata", "protocol_version": "1.10"},
        {
            "timestamp": 1000.0,
            "message": {
                "type": "TurnBegin",
                "payload": {
                    "user_input": "fix the auth refresh loop in the login handler please",
                },
            },
        },
        {
            "timestamp": 1001.0,
            "message": {
                "type": "ContentPart",
                "payload": {"type": "think", "think": "planning the fix"},
            },
        },
        {
            "timestamp": 1002.0,
            "message": {
                "type": "ContentPart",
                "payload": {
                    "type": "text",
                    "text": (
                        "The root cause is re-issuing the token on 401 without a retry "
                        "count. Here is the fix using a bounded retry."
                    ),
                },
            },
        },
        {
            "timestamp": 1003.0,
            "message": {
                "type": "ToolCall",
                "payload": {
                    "type": "function",
                    "id": "t1",
                    "function": {"name": "Shell", "arguments": "{}"},
                },
            },
        },
    ])
    arts = parse_wire(wire, project="myproj", session_id="sess-1")
    assert len(arts) == 2
    assert arts[0].meta["role"] == "user"
    assert "auth refresh" in arts[0].text
    assert arts[1].meta["role"] == "assistant"
    assert "root cause" in arts[1].text
    assert arts[0].source == "kimi"
    assert arts[0].meta["agent"] == "kimi"
    assert arts[0].meta["session_id"] == "sess-1"


def test_resolve_kimi_project_from_kimi_json(tmp_path):
    work = tmp_path / "Projects" / "stablex-saas"
    work.mkdir(parents=True)
    (work / ".git").mkdir()
    digest = hashlib.md5(str(work).encode()).hexdigest()
    kimi_json = tmp_path / "kimi.json"
    kimi_json.write_text(json.dumps({
        "work_dirs": [{"path": str(work), "kaos": "local"}],
    }))
    mapping = load_work_dir_map(kimi_json)
    assert mapping[digest] == str(work)
    wire = tmp_path / "sessions" / digest / "s1" / "wire.jsonl"
    _write_wire(wire, [])
    assert resolve_kimi_project(digest, wire, work_dir_map=mapping) == "stablex-saas"


def test_resolve_kimi_project_fallback_workdir_in_prompt(tmp_path):
    work = tmp_path / "Projects" / "portfolio"
    work.mkdir(parents=True)
    (work / ".git").mkdir()
    digest = "deadbeef" * 4
    wire = tmp_path / "sessions" / digest / "s1" / "wire.jsonl"
    _write_wire(wire, [{
        "timestamp": 1.0,
        "message": {
            "type": "TurnBegin",
            "payload": {
                "user_input": (
                    f"<git-context>\nWorking directory: {work}\n"
                    "Remote: git@example.com:x.git\n</git-context>\n"
                    "please redesign the dashboard layout for the app"
                ),
            },
        },
    }])
    assert resolve_kimi_project(digest, wire, work_dir_map={}) == "portfolio"


def test_scan_kimi_skips_subagents(tmp_path):
    work = tmp_path / "proj"
    work.mkdir()
    digest = hashlib.md5(str(work).encode()).hexdigest()
    root = tmp_path / "sessions" / digest
    sess = root / "main-session"
    _write_wire(sess / "wire.jsonl", [{
        "timestamp": 1.0,
        "message": {
            "type": "TurnBegin",
            "payload": {"user_input": "please redesign the dashboard layout carefully"},
        },
    }])
    _write_wire(sess / "subagents" / "a1" / "wire.jsonl", [{
        "timestamp": 2.0,
        "message": {
            "type": "TurnBegin",
            "payload": {"user_input": "subagent should be ignored entirely here"},
        },
    }])
    kimi_json = tmp_path / "kimi.json"
    kimi_json.write_text(json.dumps({"work_dirs": [{"path": str(work)}]}))
    found = scan_kimi_sessions(tmp_path / "sessions", kimi_json_path=kimi_json)
    assert len(found) == 1
    assert found[0][0] == sess / "wire.jsonl"
