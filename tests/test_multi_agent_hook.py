"""Tests for multi-agent hook support (Claude Code, Codex, Copilot)."""
import json
import pytest
from memor.hook_server import detect_agent, format_hook_response, handle_request
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


# --- Agent detection from request JSON ---

def test_detect_claude_code():
    req = {"cwd": "/proj", "prompt": "auth flow", "session_id": "s1"}
    assert detect_agent(req) == "claude"


def test_detect_codex():
    req = {"cwd": "/proj", "prompt": "auth flow", "session_id": "s1",
           "hook_event_name": "UserPromptSubmit", "model": "o3-pro",
           "permission_mode": "auto-edit"}
    assert detect_agent(req) == "codex"


def test_detect_copilot():
    req = {"cwd": "/proj", "prompt": "auth flow", "session_id": "s1",
           "hook_event_name": "userPromptSubmitted"}
    assert detect_agent(req) == "copilot"


def test_detect_unknown_defaults_to_claude():
    req = {"cwd": "/proj", "prompt": "hello"}
    assert detect_agent(req) == "claude"


# --- Response formatting per agent ---

def test_format_response_claude():
    resp = format_hook_response("claude", "recalled memories here")
    assert resp == {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "recalled memories here",
        }
    }


def test_format_response_codex():
    resp = format_hook_response("codex", "recalled memories here")
    assert resp == {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "recalled memories here",
        }
    }


def test_format_response_copilot():
    resp = format_hook_response("copilot", "recalled memories here")
    assert resp == {"additionalContext": "recalled memories here"}


# --- handle_request returns correct format per agent ---

def _make_db(tmp_path):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(id="a1", kind="memory", project="proj", source="distill",
                   text="use argon2 for password hashing", token_count=6,
                   created_at=100.0, meta={"mem_type": "decision", "session_id": "old"})
    s.add_artifacts([art], e.embed([art.text]))
    return db_path, e


def test_handle_request_claude_format(tmp_path):
    db_path, e = _make_db(tmp_path)
    req = {"cwd": "/proj", "prompt": "password hashing", "session_id": "s1"}
    resp = handle_request(req, db_path=db_path, embedder=e)
    assert "hookSpecificOutput" in resp
    assert resp["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_handle_request_codex_format(tmp_path):
    db_path, e = _make_db(tmp_path)
    req = {"cwd": "/proj", "prompt": "password hashing", "session_id": "s1",
           "hook_event_name": "UserPromptSubmit", "model": "o3-pro"}
    resp = handle_request(req, db_path=db_path, embedder=e)
    assert "hookSpecificOutput" in resp
    assert resp["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_handle_request_copilot_format(tmp_path):
    db_path, e = _make_db(tmp_path)
    req = {"cwd": "/proj", "prompt": "password hashing", "session_id": "s1",
           "hook_event_name": "userPromptSubmitted"}
    resp = handle_request(req, db_path=db_path, embedder=e)
    assert "additionalContext" in resp
    assert "hookSpecificOutput" not in resp


# --- install-hook config generation ---

def test_install_hook_codex(tmp_path):
    from memor.cli import _install_hook_logic_codex
    hooks_path = tmp_path / "hooks.json"
    _install_hook_logic_codex(hooks_path, "/usr/bin/memor-hook")
    data = json.loads(hooks_path.read_text())
    assert "hooks" in data
    assert "UserPromptSubmit" in data["hooks"]
    hook = data["hooks"]["UserPromptSubmit"][0]
    assert "memor-hook" in hook["command"]


def test_install_hook_copilot(tmp_path):
    from memor.cli import _install_hook_logic_copilot
    hooks_path = tmp_path / "memor.json"
    _install_hook_logic_copilot(hooks_path, "/usr/bin/memor-hook")
    data = json.loads(hooks_path.read_text())
    assert data["version"] == 1
    assert "userPromptSubmitted" in data["hooks"]
    hook = data["hooks"]["userPromptSubmitted"][0]
    assert hook["type"] == "command"
    assert "memor-hook" in hook["bash"]


def test_install_hook_codex_updates_existing(tmp_path):
    from memor.cli import _install_hook_logic_codex
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(json.dumps({
        "hooks": {"UserPromptSubmit": [{"command": "old-hook", "timeout": 10}]}
    }))
    _install_hook_logic_codex(hooks_path, "/usr/bin/memor-hook")
    data = json.loads(hooks_path.read_text())
    entries = data["hooks"]["UserPromptSubmit"]
    memor_entries = [e for e in entries if "memor-hook" in e.get("command", "")]
    assert len(memor_entries) == 1
    old_entries = [e for e in entries if "old-hook" in e.get("command", "")]
    assert len(old_entries) == 1
