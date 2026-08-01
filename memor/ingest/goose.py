"""Ingest Goose sessions from ~/.local/share/goose/sessions/sessions.db."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from memor.ingest.claude_code import _signal_score, _strip_system_reminders
from memor.project import resolve_project
from memor.redact import redact_text
from memor.tokencount import count_tokens
from memor.types import Artifact

GOOSE_DB_PATH = Path.home() / ".local" / "share" / "goose" / "sessions" / "sessions.db"


def _parse_updated_at(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _created_at(ts) -> float:
    if ts is None:
        return 0.0
    try:
        v = float(ts)
    except (TypeError, ValueError):
        return 0.0
    # Heuristic: ms timestamps are > 1e12
    if v > 1e12:
        return v / 1000.0
    return v


def _text_from_content_json(content_json: str) -> str:
    try:
        blocks = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(blocks, str):
        return blocks.strip()
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            t = block.get("text") or ""
            if t:
                parts.append(t)
    return "\n".join(parts).strip()


def parse_session(
    db_path: Path,
    session_id: str,
    project: str,
    *,
    filter_noise: bool = True,
) -> list[Artifact]:
    """Parse one Goose session's text messages into session_chunk artifacts."""
    if not db_path.is_file():
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, message_id, role, content_json, created_timestamp "
            "FROM messages WHERE session_id = ? ORDER BY created_timestamp, id",
            (session_id,),
        ).fetchall()
    finally:
        con.close()

    arts: list[Artifact] = []
    seen_hashes: set[str] = set()
    for i, row in enumerate(rows):
        role = (row["role"] or "").strip()
        if role not in ("user", "assistant"):
            continue
        text = _text_from_content_json(row["content_json"] or "")
        if not text:
            # toolRequest / toolResponse and empty text skipped
            continue
        text = _strip_system_reminders(text).strip()
        if not text:
            continue
        text, _ = redact_text(text)
        if not text.strip():
            continue

        text_hash = hashlib.md5(text.encode()).hexdigest()
        if text_hash in seen_hashes:
            continue
        seen_hashes.add(text_hash)

        token_count = max(1, count_tokens(text))
        if filter_noise and _signal_score(text, role, token_count) == 0:
            continue

        msg_key = row["message_id"] or row["id"] or i
        arts.append(Artifact(
            id=f"{session_id}:{msg_key}",
            kind="session_chunk",
            project=project,
            source="goose",
            text=text,
            token_count=token_count,
            created_at=_created_at(row["created_timestamp"]),
            meta={
                "session_id": session_id,
                "role": role,
                "ord": i,
                "agent": "goose",
            },
        ))
    return arts


def scan_goose_sessions(
    db_path: Path = GOOSE_DB_PATH,
) -> list[tuple[str, str, float]]:
    """Return (session_id, project_name, updated_at_epoch) for each Goose session."""
    results: list[tuple[str, str, float]] = []
    if not db_path.is_file():
        return results
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, working_dir, updated_at FROM sessions ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    for row in rows:
        sid = row["id"]
        if not sid:
            continue
        cwd = row["working_dir"] or ""
        project = resolve_project(cwd) if cwd else "unknown"
        mtime = _parse_updated_at(row["updated_at"])
        results.append((sid, project, mtime))
    return results


def goose_state_key(session_id: str) -> str:
    return f"goose:{session_id}"
