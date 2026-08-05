"""Ingest Kimi Code CLI sessions from ~/.kimi/sessions/**/wire.jsonl."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from memor.ingest.claude_code import _signal_score, _strip_system_reminders
from memor.project import resolve_project
from memor.redact import redact_text
from memor.tokencount import count_tokens
from memor.types import Artifact

KIMI_SESSIONS_DIR = Path.home() / ".kimi" / "sessions"
KIMI_JSON_PATH = Path.home() / ".kimi" / "kimi.json"

_WORKDIR_RE = re.compile(r"Working directory:\s*(\S+)", re.I)


def _user_input_text(value) -> str:
    """Flatten a TurnBegin ``user_input`` to text, whatever shape it arrived in.

    Kimi sends a plain string for a typed message, but a list of content blocks
    when the turn carries an attachment -- a screenshot pasted into the prompt
    arrives as ``[{"type": "text", ...}, {"type": "image_url", ...}]``. Calling
    string methods on that list raised, and because the daemon records a file's
    state only after a successful parse, the two affected sessions were retried
    on every poll forever, re-running the whole post-ingest pipeline each time.

    Only text blocks are kept: an image carries no text to embed, and its
    base64 payload would be megabytes of noise.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(p for p in parts if p)
    return ""


def load_work_dir_map(kimi_json_path: Path = KIMI_JSON_PATH) -> dict[str, str]:
    """Map md5(work_dir path) -> absolute work_dir path from kimi.json."""
    if not kimi_json_path.exists():
        return {}
    try:
        data = json.loads(kimi_json_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, str] = {}
    for entry in data.get("work_dirs") or []:
        path = entry.get("path") if isinstance(entry, dict) else None
        if not path:
            continue
        out[hashlib.md5(path.encode()).hexdigest()] = path
    return out


def resolve_kimi_project(
    project_md5: str,
    wire_path: Path,
    *,
    work_dir_map: dict[str, str] | None = None,
) -> str:
    mapping = work_dir_map if work_dir_map is not None else load_work_dir_map()
    if project_md5 in mapping:
        return resolve_project(mapping[project_md5])
    cwd = _workdir_from_wire(wire_path)
    if cwd:
        return resolve_project(cwd)
    return "unknown"


def _workdir_from_wire(wire_path: Path) -> str | None:
    try:
        lines = wire_path.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") or {}
        if msg.get("type") != "TurnBegin":
            continue
        user_input = _user_input_text((msg.get("payload") or {}).get("user_input"))
        m = _WORKDIR_RE.search(user_input)
        if m:
            return m.group(1).strip()
    return None


def parse_wire(
    path: Path,
    project: str,
    *,
    filter_noise: bool = True,
    session_id: str | None = None,
) -> list[Artifact]:
    """Parse a Kimi wire.jsonl into session_chunk artifacts."""
    sid = session_id or path.parent.name
    arts: list[Artifact] = []
    seen_hashes: set[str] = set()
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg = rec.get("message") or {}
        msg_type = msg.get("type")
        payload = msg.get("payload") or {}
        role = ""
        text = ""

        if msg_type == "TurnBegin":
            role = "user"
            text = _user_input_text(payload.get("user_input")).strip()
        elif msg_type == "ContentPart" and payload.get("type") == "text":
            role = "assistant"
            text = (payload.get("text") or "")
            text = text.strip() if isinstance(text, str) else ""
        else:
            continue

        if not text:
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

        created_at = float(rec.get("timestamp") or 0.0)
        arts.append(Artifact(
            id=f"{sid}:{i}",
            kind="session_chunk",
            project=project,
            source="kimi",
            text=text,
            token_count=token_count,
            created_at=created_at,
            meta={
                "session_id": sid,
                "role": role,
                "ord": i,
                "agent": "kimi",
            },
        ))
    return arts


def scan_kimi_sessions(
    sessions_dir: Path = KIMI_SESSIONS_DIR,
    *,
    kimi_json_path: Path = KIMI_JSON_PATH,
) -> list[tuple[Path, str, str]]:
    """Return (wire_path, project_name, session_id) for each session wire.jsonl.

    Skips subagent wires under */subagents/*.
    """
    results: list[tuple[Path, str, str]] = []
    if not sessions_dir.is_dir():
        return results
    work_dir_map = load_work_dir_map(kimi_json_path)
    for project_dir in sorted(sessions_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        project_md5 = project_dir.name
        for session_dir in sorted(project_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            wire = session_dir / "wire.jsonl"
            if not wire.is_file():
                continue
            # Skip nested subagent wires (not at session root)
            if "subagents" in wire.parts:
                continue
            project = resolve_kimi_project(
                project_md5, wire, work_dir_map=work_dir_map
            )
            results.append((wire, project, session_dir.name))
    return results
