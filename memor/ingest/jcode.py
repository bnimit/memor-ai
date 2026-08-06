"""Ingest jcode sessions.

jcode is an agentic coding environment in the same family as Claude Code, and
its sessions carry exactly the material memor exists to remember. It stores one
JSON file per session under ``~/.jcode/sessions``, plus an optional
``.journal.jsonl`` sidecar holding incremental appends for the live session.

Two shapes have to be handled because they disagree about where things live:

* ``<id>.json`` — the settled session. ``messages`` is a flat list, and
  ``working_dir`` is at the top level, but it is frequently ``null``.
* ``<id>.journal.jsonl`` — one record per append while the session is running.
  Messages arrive under ``append_messages``, and the working directory lives in
  ``meta.working_dir``, where it is reliably populated.

The journal is therefore the better source of project scope. A session whose
JSON has no ``working_dir`` would otherwise land in "unknown" and leak across
project boundaries at recall time, so the journal is consulted first and the
env-provided cwd (from the ``turn_end`` hook) overrides both.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from memor.ingest.claude_code import _signal_score, _strip_system_reminders
from memor.redact import redact_text
from memor.tokencount import count_tokens
from memor.types import Artifact

JCODE_SESSIONS_DIR = Path.home() / ".jcode" / "sessions"

#: Reasoning traces are the model thinking aloud, not a record of what was
#: decided or done, and tool payloads are already captured as their own records
#: by the agent. Only prose is worth remembering.
_TEXT_BLOCK = "text"


def _text_of(content) -> str:
    """Flatten message content to prose, dropping tool and reasoning blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == _TEXT_BLOCK:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(p for p in parts if p)


def journal_path(session_path: Path) -> Path:
    """The journal sidecar for a session JSON, whether or not it exists."""
    return session_path.with_suffix("").with_suffix(".journal.jsonl") \
        if session_path.name.endswith(".json") \
        else session_path


def working_dir_for(session_path: Path) -> str:
    """Best available working directory for a session.

    The session JSON's ``working_dir`` is often null; the journal's
    ``meta.working_dir`` is not. Preferring the journal is what keeps a session
    from being filed under "unknown" and recalled into unrelated projects.
    """
    jp = session_path.parent / (session_path.stem + ".journal.jsonl")
    if jp.exists():
        try:
            for line in jp.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                wd = (rec.get("meta") or {}).get("working_dir")
                if wd:
                    return str(wd)
        except OSError:
            pass
    try:
        data = json.loads(session_path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("working_dir") or "")


def _messages(session_path: Path) -> list[dict]:
    """Every message for a session, from the settled JSON and its journal.

    Both are read because the JSON is the durable record while the journal
    holds appends that have not been folded in yet. Duplicates are removed by
    content later, so reading both costs nothing but completeness.
    """
    out: list[dict] = []
    try:
        data = json.loads(session_path.read_text(errors="replace"))
        out.extend(data.get("messages") or [])
    except (OSError, json.JSONDecodeError):
        pass

    jp = session_path.parent / (session_path.stem + ".journal.jsonl")
    if jp.exists():
        try:
            for line in jp.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out.extend(rec.get("append_messages") or [])
        except OSError:
            pass
    return out


def parse_session(
    path: Path,
    project: str,
    *,
    filter_noise: bool = True,
    session_id: str | None = None,
) -> list[Artifact]:
    """Parse one jcode session into session_chunk artifacts."""
    sid = session_id or path.stem
    arts: list[Artifact] = []
    seen_hashes: set[str] = set()

    try:
        created_at = path.stat().st_mtime
    except OSError:
        created_at = 0.0

    for i, msg in enumerate(_messages(path)):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or ""
        if role not in ("user", "assistant"):
            continue
        text = _text_of(msg.get("content")).strip()
        if not text:
            continue
        # jcode wraps injected context in <system-reminder> blocks: harness
        # scaffolding addressed to the model, not anything a future session
        # could usefully be reminded of.
        text = _strip_system_reminders(text).strip()
        if not text:
            continue
        text, _ = redact_text(text)
        text = text.strip()
        if not text:
            continue

        text_hash = hashlib.md5(text.encode()).hexdigest()
        if text_hash in seen_hashes:
            continue
        seen_hashes.add(text_hash)

        token_count = max(1, count_tokens(text))
        if filter_noise and _signal_score(text, role, token_count) == 0:
            continue

        arts.append(Artifact(
            id=f"{sid}:{i}",
            kind="session_chunk",
            project=project,
            source="jcode",
            text=text,
            token_count=token_count,
            created_at=created_at,
            meta={
                "session_id": sid,
                "role": role,
                "ord": i,
                "agent": "jcode",
            },
        ))
    return arts


def scan_jcode_sessions(
    sessions_dir: Path = JCODE_SESSIONS_DIR,
) -> list[tuple[Path, str, str]]:
    """Return (session_path, project_name, session_id) for each jcode session.

    Backups (``.bak``) and journals are skipped: the journal is read through its
    session, never as a unit of its own, so a session is ingested exactly once.
    """
    from memor.project import resolve_project

    results: list[tuple[Path, str, str]] = []
    if not sessions_dir.is_dir():
        return results
    for path in sorted(sessions_dir.glob("*.json")):
        if path.name.endswith(".bak") or ".journal." in path.name:
            continue
        wd = working_dir_for(path)
        project = resolve_project(wd) if wd else "unknown"
        results.append((path, project, path.stem))
    return results
