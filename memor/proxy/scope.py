"""Work out which project a proxied request belongs to, from the request itself.

A hook is launched by the agent inside a working directory, so it knows the
project for free. A proxy is handed an HTTP request and nothing else. The
original code asked for an ``x-memor-project`` header, no client sends one, and
so every proxied request resolved to ``"unknown"`` -- a project with zero
artifacts. Recall ran on all 4,056 requests over four days and returned nothing
every time.

The request does carry the answer, twice over. Agents state their working
directory in the system prompt, and the conversation is full of absolute file
paths in tool calls. Both are read here, cheapest and most explicit first:

1. an explicit working-directory line in the system prompt
2. the git root shared by the absolute paths the conversation touched

Nothing here guesses. If neither yields a directory that exists on disk with a
git root, the caller keeps its existing fallback.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

#: How agents state the working directory. Matched case-insensitively; a form
#: that does not appear here simply falls through to the path evidence.
_CWD_LABEL = re.compile(
    r"(?:working directory|cwd|workspace(?:\s+root)?|project\s+root)\s*[:=]\s*"
    r"[\"'`]?(/[^\s\"'`\n<>]+)",
    re.IGNORECASE,
)

#: Tool inputs that name a file. Mirrors memor.trajectory.
_PATH_KEYS = ("file_path", "notebook_path", "path", "filePath", "cwd")

#: Ceiling on the system text scanned for a working-directory line. System
#: prompts run to tens of kilobytes and the declaration is near the top.
_SYSTEM_SCAN_CHARS = 8_000

#: Ceiling on tool calls inspected for paths. This runs on the request path
#: alongside compression, and a long conversation must not make it slower.
_MAX_TOOL_BLOCKS = 400


def _system_text(body: dict) -> str:
    system = body.get("system")
    if isinstance(system, str):
        return system[:_SYSTEM_SCAN_CHARS]
    if isinstance(system, list):
        parts = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
            if sum(len(p) for p in parts) > _SYSTEM_SCAN_CHARS:
                break
        return "\n".join(parts)[:_SYSTEM_SCAN_CHARS]
    return ""


def _declared_directory(body: dict) -> str | None:
    """A working directory the agent stated outright."""
    for text in (_system_text(body), _first_user_text(body)):
        if not text:
            continue
        match = _CWD_LABEL.search(text)
        if match:
            return match.group(1)
    return None


def _first_user_text(body: dict) -> str:
    """Agents often restate the environment in the opening user turn."""
    for message in body.get("messages", []) or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content[:_SYSTEM_SCAN_CHARS]
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")[:_SYSTEM_SCAN_CHARS]
        return ""
    return ""


def _touched_paths(body: dict) -> list[str]:
    """Absolute paths named by tool calls, newest first."""
    found: list[str] = []
    seen = 0
    for message in reversed(body.get("messages", []) or []):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            seen += 1
            if seen > _MAX_TOOL_BLOCKS:
                return found
            params = block.get("input")
            if not isinstance(params, dict):
                continue
            for key in _PATH_KEYS:
                value = params.get(key)
                if isinstance(value, str) and value.startswith("/"):
                    found.append(value)
                    break
    return found


def _project_for(directory: str) -> str | None:
    """Resolve a directory to a project name, or None if it is not one.

    Deliberately strict: the path has to exist and sit inside a git checkout.
    A plausible-looking string that is not a real repository is worse than no
    answer, because it would scope recall to a bucket that cannot be right.
    """
    try:
        path = Path(directory)
        if not path.exists():
            path = path.parent
            if not path.exists():
                return None
        from memor.project import _find_git_root

        root = _find_git_root(path.resolve())
        return root.name if root else None
    except (OSError, ValueError):
        return None


def resolve_request_project(project_hint: str, body: dict) -> str:
    """Project for a proxied request: explicit header, else the body's evidence."""
    if project_hint:
        from memor.project import resolve_project

        return resolve_project(project_hint)
    return project_from_body(body) or "unknown"


def project_from_body(body: dict) -> str | None:
    """Best available project for a proxied request, or None if undeterminable."""
    if not isinstance(body, dict):
        return None

    declared = _declared_directory(body)
    if declared:
        project = _project_for(declared)
        if project:
            return project

    # Fall back to where the work actually happened. The mode rather than the
    # newest path: one stray read outside the tree should not move the scope.
    votes = Counter()
    for raw in _touched_paths(body):
        project = _project_for(raw)
        if project:
            votes[project] += 1
    if votes:
        return votes.most_common(1)[0][0]
    return None
