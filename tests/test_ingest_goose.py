"""Tests for Goose sessions.db ingest."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from memor.ingest.goose import (
    goose_state_key,
    parse_session,
    scan_goose_sessions,
)
from memor.ingest.sources import scan_all_sources


def _make_goose_db(path: Path, *, working_dir: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            working_dir TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content_json TEXT NOT NULL,
            created_timestamp INTEGER NOT NULL
        );
    """)
    con.execute(
        "INSERT INTO sessions(id, name, working_dir, updated_at) VALUES (?,?,?,?)",
        ("sess-1", "Review", working_dir, "2026-07-12 10:10:18"),
    )
    msgs = [
        ("m1", "user", [{"type": "text", "text":
            "Can you review the blofin automated trading bot and confirm LLM decisions work?"}],
         100),
        ("m2", "assistant", [{"type": "text", "text":
            "The approach is to map every trade path, then verify the LLM risk gate "
            "before orders. Here is a structured review of the automation surface."}],
         101),
        ("m3", "assistant", [{"type": "toolRequest", "id": "c1",
                              "toolCall": {"status": "success", "value": {"name": "todo"}}}],
         102),
        ("m4", "user", [{"type": "toolResponse", "id": "c1",
                         "toolResult": {"status": "success", "value": {"content": []}}}],
         103),
        ("m5", "assistant", [{"type": "text", "text":
            "We decided to keep the risk gate before every LLM trade decision."}],
         104),
    ]
    for mid, role, content, ts in msgs:
        con.execute(
            "INSERT INTO messages(message_id, session_id, role, content_json, created_timestamp) "
            "VALUES (?,?,?,?,?)",
            (mid, "sess-1", role, json.dumps(content), ts),
        )
    con.commit()
    con.close()


def test_parse_session_skips_tool_messages(tmp_path):
    proj = tmp_path / "blofin-bot"
    proj.mkdir()
    (proj / ".git").mkdir()
    db = tmp_path / "sessions.db"
    _make_goose_db(db, working_dir=str(proj))
    arts = parse_session(db, "sess-1", project="blofin-bot")
    assert len(arts) == 3
    roles = [a.meta["role"] for a in arts]
    assert roles == ["user", "assistant", "assistant"]
    assert all(a.source == "goose" for a in arts)
    assert all(a.meta["agent"] == "goose" for a in arts)
    assert "risk gate" in arts[2].text


def test_scan_goose_sessions(tmp_path):
    proj = tmp_path / "blofin-bot"
    proj.mkdir()
    (proj / ".git").mkdir()
    db = tmp_path / "sessions.db"
    _make_goose_db(db, working_dir=str(proj))
    found = scan_goose_sessions(db)
    assert len(found) == 1
    sid, project, mtime = found[0]
    assert sid == "sess-1"
    assert project == "blofin-bot"
    assert mtime > 0
    assert goose_state_key(sid) == "goose:sess-1"


def test_scan_all_sources_includes_goose_and_skips_missing(tmp_path):
    proj = tmp_path / "blofin-bot"
    proj.mkdir()
    (proj / ".git").mkdir()
    db = tmp_path / "sessions.db"
    _make_goose_db(db, working_dir=str(proj))
    units = scan_all_sources(
        claude_projects_dir=tmp_path / "no-claude",
        kimi_sessions_dir=tmp_path / "no-kimi",
        goose_db_path=db,
    )
    assert len(units) == 1
    assert units[0].agent == "goose"
    arts = units[0].parse()
    assert len(arts) == 3
