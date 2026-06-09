"""Tests for multi-agent hook support (Claude Code, Codex, Copilot)."""
import json
import sqlite3
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


# --- Agent tracking in recall_log ---

def test_log_recall_stores_agent(tmp_path):
    db_path = str(tmp_path / "m.db")
    s = SqliteStore(db_path, dim=16)
    s.log_recall("proj", "query", 1, 0.8, 50, 5.0, "ok", "s1", agent="codex")
    row = s.db.execute("SELECT agent FROM recall_log").fetchone()
    assert row["agent"] == "codex"


def test_log_recall_defaults_to_claude(tmp_path):
    db_path = str(tmp_path / "m.db")
    s = SqliteStore(db_path, dim=16)
    s.log_recall("proj", "query", 1, 0.8, 50, 5.0, "ok", "s1")
    row = s.db.execute("SELECT agent FROM recall_log").fetchone()
    assert row["agent"] == "claude"


def test_agent_breakdown(tmp_path):
    db_path = str(tmp_path / "m.db")
    s = SqliteStore(db_path, dim=16)
    s.log_recall("proj", "q1", 1, 0.8, 50, 5.0, "ok", "s1", agent="claude")
    s.log_recall("proj", "q2", 1, 0.7, 40, 6.0, "ok", "s2", agent="claude")
    s.log_recall("proj", "q3", 0, 0.0, 0, 3.0, "no_hits", "s3", agent="codex")
    breakdown = s.get_agent_breakdown()
    agents = {r["agent"]: r for r in breakdown}
    assert agents["claude"]["recalls"] == 2
    assert agents["claude"]["hits"] == 2
    assert agents["codex"]["recalls"] == 1
    assert agents["codex"]["hits"] == 0


def test_handle_request_logs_agent(tmp_path):
    """handle_request should persist the detected agent in recall_log."""
    db_path, e = _make_db(tmp_path)
    req = {"cwd": "/proj", "prompt": "password hashing", "session_id": "s1",
           "hook_event_name": "userPromptSubmitted"}
    handle_request(req, db_path=db_path, embedder=e)
    store = SqliteStore(db_path, dim=16)
    row = store.db.execute("SELECT agent FROM recall_log").fetchone()
    assert row["agent"] == "copilot"


def test_migrate_agent_column(tmp_path):
    """Opening a store with a pre-existing recall_log without agent column should add it."""
    db_path = str(tmp_path / "m.db")
    db = sqlite3.connect(db_path)
    db.execute("""CREATE TABLE IF NOT EXISTS recall_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL, project TEXT, query_preview TEXT,
        hits_count INTEGER, top_score REAL, tokens_injected INTEGER,
        latency_ms REAL, status TEXT, session_id TEXT)""")
    db.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta(key, value) VALUES('dim', '16')")
    db.commit()
    db.close()
    s = SqliteStore(db_path, dim=16)
    cols = [r[1] for r in s.db.execute("PRAGMA table_info(recall_log)").fetchall()]
    assert "agent" in cols
