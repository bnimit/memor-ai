"""Ingest Codex CLI / Codex Desktop sessions.

memor already *recalls into* Codex -- the skill and the retrieve tool are wired
for it, and the recall ledger shows 38 recalls served to `agent='codex'`. It
never ingested *from* it. Codex was therefore a consumer of everyone else's
memory and a contributor of none of its own, which is precisely the asymmetry a
shared layer exists to remove: work done in Codex was unrecallable anywhere,
including in Codex.

Codex writes one JSONL rollout per session under
``~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<iso>-<uuid>.jsonl``. Three record
types appear at the top level, distinguished by ``type``:

* ``session_meta`` -- first line, carries ``payload.cwd``. This is the only
  reliable project scope, so a rollout without it is filed as "unknown" rather
  than guessed at from the path.
* ``response_item`` -- the transcript. ``payload.type == "message"`` holds the
  prose, with content blocks of ``input_text`` (user) and ``output_text``
  (assistant).
* ``event_msg`` -- UI-level echoes of the same turns. Deliberately skipped: they
  duplicate ``response_item`` content and would double every artifact.

Two shapes here are unlike the other agents and drive the filtering below:

* **Tool calls are inlined into assistant prose**, not carried as structured
  records. Codex Desktop emits ``[external_agent_tool_call: Bash]`` and
  ``[external_agent_tool_result]`` blocks as ordinary ``output_text``. On the
  local corpus this is the bulk of the 41,891 assistant messages, and it is
  exactly the machine chatter the compressor refuses to store elsewhere. It is
  dropped, so what remains is reasoning and conclusions.
* **The ``developer`` role** carries harness instructions, not user intent, and
  is skipped for the same reason ``<system-reminder>`` blocks are stripped from
  the jcode path.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from memor.ingest.claude_code import _signal_score, _strip_system_reminders
from memor.redact import redact_text
from memor.tokencount import count_tokens
from memor.types import Artifact

CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"

#: Codex Desktop inlines tool traffic into assistant prose as bracketed blocks.
#: A message that *is* one of these is a tool call or its output, which the
#: store does not keep for any other agent either. Anchored at the start so a
#: message that merely mentions the syntax while explaining it is kept.
_TOOL_BLOCK_RE = re.compile(
    r"^\s*\[external_agent_tool_(?:call|result)\b", re.I)

#: Only these content blocks hold prose. Reasoning blocks are the model
#: thinking aloud rather than a record of what was decided, matching the jcode
#: parser's rule.
_TEXT_BLOCKS = ("input_text", "output_text")


def _text_of(content) -> str:
    """Flatten Codex content blocks to prose."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in _TEXT_BLOCKS:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(p for p in parts if p)


def _records(path: Path):
    """Yield parsed JSONL records, skipping malformed lines.

    A rollout for a live session can end mid-write, so a truncated final line is
    expected rather than exceptional and must not lose the whole session.
    """
    try:
        with path.open(errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def working_dir_for(path: Path) -> str:
    """The cwd recorded in ``session_meta``, or "" when absent.

    Read from the record rather than inferred from the rollout's path, which
    encodes only the date. Returning "" lets the caller file the session under
    "unknown" instead of leaking it into an unrelated project at recall time.
    """
    for rec in _records(path):
        if rec.get("type") == "session_meta":
            return str((rec.get("payload") or {}).get("cwd") or "")
    return ""


def parse_session(
    path: Path,
    project: str,
    *,
    filter_noise: bool = True,
    session_id: str | None = None,
) -> list[Artifact]:
    """Parse one Codex rollout into session_chunk artifacts."""
    sid = session_id or path.stem
    arts: list[Artifact] = []
    seen_hashes: set[str] = set()

    try:
        created_at = path.stat().st_mtime
    except OSError:
        created_at = 0.0

    for i, rec in enumerate(_records(path)):
        if rec.get("type") != "response_item":
            continue
        payload = rec.get("payload") or {}
        if payload.get("type") != "message":
            continue
        role = payload.get("role") or ""
        # 'developer' is harness instruction, not user intent.
        if role not in ("user", "assistant"):
            continue

        text = _text_of(payload.get("content")).strip()
        if not text or _TOOL_BLOCK_RE.match(text):
            continue
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
            source="codex",
            text=text,
            token_count=token_count,
            created_at=created_at,
            meta={
                "session_id": sid,
                "role": role,
                "ord": i,
                "agent": "codex",
            },
        ))
    return arts


def scan_codex_sessions(
    sessions_dir: Path = CODEX_SESSIONS_DIR,
) -> list[tuple[Path, str, str]]:
    """Return (rollout_path, project_name, session_id) for each Codex session.

    Rollouts are nested under year/month/day, so the glob is recursive.
    """
    from memor.project import resolve_project

    results: list[tuple[Path, str, str]] = []
    if not sessions_dir.is_dir():
        return results
    for path in sorted(sessions_dir.rglob("rollout-*.jsonl")):
        wd = working_dir_for(path)
        project = resolve_project(wd) if wd else "unknown"
        results.append((path, project, path.stem))
    return results
