"""Retrieval signals from the agent's trajectory, not just the prompt.

The measured retrieval failure is query-side. The median user prompt in this
store is 152 characters, and 152 characters is a thin thing to match a
23k-chunk corpus against. The trajectory the agent has already produced -- the
files it just read, the command that just failed -- is far more specific than
the prompt that follows it, and it is already sitting on disk.

Everything here is deterministic string work over the tail of a transcript: no
model, no network, nothing that can fail slowly. The recall hot path budgets
under 15ms end to end, so the transcript is read backwards from the end under a
byte cap rather than parsed whole, and the work is bounded by the cap rather
than by session length. A session that has run for days costs the same as one
that started a minute ago.

Off by default. Set MEMOR_TRAJECTORY_QUERY=1 to enable.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

#: How much of the transcript tail to read. Roughly the last few exchanges,
#: which is the horizon over which "what am I working on" is still true.
MAX_TAIL_BYTES = 32_768

#: Caps on what reaches the query. The prompt has to stay the dominant term --
#: these are meant to sharpen the embedding, not to replace it.
MAX_FILES = 5
MAX_ERROR_CHARS = 160
MAX_APPENDED_CHARS = 320

#: Tool inputs that name a file. Different agents use different keys.
_PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")

#: A tool result carrying one of these is worth putting in the query: it names
#: the thing that is currently broken, which is usually what the next prompt is
#: about. Matched case-insensitively against the head of the result.
_ERROR_MARKERS = (
    "traceback (most recent call last)",
    "syntaxerror", "typeerror", "valueerror", "attributeerror", "keyerror",
    "importerror", "modulenotfounderror", "assertionerror",
    "error:", "error[", "fatal:", "panic:", "exception:",
    "failed", "failure", "cannot find", "not found", "undefined",
)

#: Scanned when deciding whether a result is an error report. Bounded so a
#: 200k-line log costs the same as a short one.
_ERROR_SCAN_CHARS = 400


@dataclass
class TrajectorySignals:
    """What the agent has been doing, most recent first."""

    files: list[str] = field(default_factory=list)
    error: str = ""

    def __bool__(self) -> bool:
        return bool(self.files or self.error)


def enabled() -> bool:
    return os.environ.get("MEMOR_TRAJECTORY_QUERY", "0").lower() in ("1", "true", "yes")


def read_tail(path: str | Path, max_bytes: int = MAX_TAIL_BYTES) -> list[str]:
    """Return whole lines from the last ``max_bytes`` of a file, oldest first.

    The first line of the window is dropped unless the window starts at byte 0,
    because a mid-line start would decode as a broken JSON fragment.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
        with p.open("rb") as fh:
            start = max(0, size - max_bytes)
            fh.seek(start)
            chunk = fh.read()
    except OSError:
        return []
    text = chunk.decode("utf-8", "replace")
    lines = text.split("\n")
    if start > 0 and lines:
        lines = lines[1:]
    return [ln for ln in lines if ln.strip()]


def _short_path(file_path: str) -> str:
    """Last two segments: enough to disambiguate, short enough not to dominate.

    A bare basename loses the package a file belongs to, and a full absolute
    path spends most of its characters on a home directory shared by every
    other candidate.
    """
    parts = [seg for seg in Path(file_path).parts if seg not in ("/", "\\")]
    return "/".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else "")


def _looks_like_error(text: str) -> bool:
    head = text[:_ERROR_SCAN_CHARS].lower()
    return any(marker in head for marker in _ERROR_MARKERS)


def _first_error_line(text: str) -> str:
    """The most informative line of an error blob.

    A traceback's useful line is the last one, not the first -- the first is
    only ever "Traceback (most recent call last):". Everything else is reported
    from the top.
    """
    lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()]
    if not lines:
        return ""
    if lines[0].lower().startswith("traceback"):
        return lines[-1][:MAX_ERROR_CHARS]
    for ln in lines:
        if _looks_like_error(ln):
            return ln[:MAX_ERROR_CHARS]
    return lines[0][:MAX_ERROR_CHARS]


def _blocks(record: dict) -> list:
    content = record.get("message", {}).get("content")
    return content if isinstance(content, list) else []


def extract_signals(transcript_path: str | Path,
                    *, max_bytes: int = MAX_TAIL_BYTES,
                    max_files: int = MAX_FILES) -> TrajectorySignals:
    """Recent file paths and the latest error, from the tail of a transcript."""
    files: list[str] = []
    error = ""

    for line in reversed(read_tail(transcript_path, max_bytes)):
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(record, dict):
            continue

        for block in _blocks(record):
            if not isinstance(block, dict):
                continue
            kind = block.get("type")

            if kind == "tool_use":
                params = block.get("input")
                if not isinstance(params, dict):
                    continue
                for key in _PATH_KEYS:
                    raw = params.get(key)
                    if isinstance(raw, str) and raw.strip():
                        short = _short_path(raw)
                        if short and short not in files:
                            files.append(short)
                        break

            elif kind == "tool_result" and not error:
                body = block.get("content")
                if isinstance(body, list):
                    body = " ".join(b.get("text", "") for b in body
                                    if isinstance(b, dict) and b.get("type") == "text")
                if isinstance(body, str) and body.strip() and _looks_like_error(body):
                    error = _first_error_line(body)

        if len(files) >= max_files and error:
            break

    return TrajectorySignals(files=files[:max_files], error=error)


def build_query(prompt: str, signals: TrajectorySignals,
                *, max_appended: int = MAX_APPENDED_CHARS) -> str:
    """Fold trajectory signals into the retrieval query.

    The prompt stays first and stays whole. Signals are appended, and the whole
    appendix is truncated rather than any single signal, so a long error message
    cannot crowd out the file list.
    """
    if not signals:
        return prompt
    parts = []
    if signals.files:
        parts.append("files: " + ", ".join(signals.files))
    if signals.error:
        parts.append("error: " + signals.error)
    if not parts:
        return prompt
    appendix = " | ".join(parts)[:max_appended]
    return f"{prompt} | {appendix}" if prompt else appendix


def enrich_from_transcript(prompt: str, transcript_path: str | Path | None) -> str:
    """Whole path, guarded. Any failure returns the prompt unchanged.

    This runs inline on every recall, so it must never raise and never block:
    a missing transcript, a truncated write mid-read, a payload from an agent
    that shapes its records differently -- all of them mean "no signal", which
    is exactly the behaviour before this existed.
    """
    if not transcript_path or not enabled():
        return prompt
    try:
        return build_query(prompt, extract_signals(transcript_path))
    except Exception:
        return prompt
