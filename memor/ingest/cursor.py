"""Ingest Cursor composer sessions from Cursor's local VS Code state stores.

Cursor is the one wired agent memor could not read from at all. Its chat
traffic rides a private binary protobuf wire on ``*.api5.cursor.sh`` behind a
request checksum, so interception buys nothing that survives a Cursor update.
Everything worth remembering is already on disk, so this reads that instead.

Two stores are involved:

``globalStorage/state.vscdb``
    The corpus. ``cursorDiskKV`` holds one JSON blob per message under
    ``bubbleId:<composerId>:<bubbleId>``, so thread grouping falls out of the
    key and needs no join. This database is multi-gigabyte and is usually open
    in a running Cursor, hence the immutable reads below.

``workspaceStorage/<hash>/``
    ``workspace.json`` names the folder and ``state.vscdb`` lists that
    workspace's composer ids, which is the only authoritative composer to
    project mapping Cursor keeps.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from memor.ingest.claude_code import _signal_score, _strip_system_reminders
from memor.project import resolve_project
from memor.redact import redact_text
from memor.tokencount import count_tokens
from memor.types import Artifact

CURSOR_USER_DIR = Path.home() / "Library" / "Application Support" / "Cursor" / "User"
CURSOR_GLOBAL_DB = CURSOR_USER_DIR / "globalStorage" / "state.vscdb"
CURSOR_WORKSPACE_DIR = CURSOR_USER_DIR / "workspaceStorage"

# Bubble ``type``. Cursor stores no role string; the integer is the only marker.
_TYPE_USER = 1
_TYPE_ASSISTANT = 2
_ROLE_BY_TYPE = {_TYPE_USER: "user", _TYPE_ASSISTANT: "assistant"}

# Pulled off the raw JSON rather than the parsed object: the fallback only
# needs the first workspace-looking path, and most bubbles are large.
#
# Paths appear at two nesting depths. Structured fields like a code block's
# uri.path are plain JSON, but a tool's arguments are themselves a JSON string
# inside the record, so their quotes arrive backslash-escaped. Matching only
# the plain form missed every tool-only thread, which is most of them.
_PATH_RE = re.compile(r'\\?"(?:path|targetFile|effectiveUri|fsPath)\\?"\s*:\s*\\?"(/[^"\\]+)')

# Tool output is truncated per block. A single bubble can carry a full file
# read, and the point of ingesting tool results is the outcome, not the bulk.
_TOOL_RESULT_LIMIT = 2000

# Args are a one-line call signature, not payload; enough to see which file or
# command a turn touched without pulling a whole patch in twice.
_TOOL_ARGS_LIMIT = 300

# A tool turn is kept on substance rather than phrasing. Below this it is a
# bare call signature with no outcome attached, which recalls nothing.
_MIN_TOOL_TOKENS = 12


def _connect_ro(path: Path) -> sqlite3.Connection:
    """Open a Cursor state DB without disturbing a running Cursor.

    ``immutable=1`` matters as much as ``mode=ro`` here: the global store is
    ~4GB with a live WAL whenever Cursor is open, and a plain read-only
    connection can still block on or attempt WAL recovery. Immutable promises
    the file will not change under us, so SQLite skips locking entirely. The
    cost is that a session written mid-scan is picked up on the next poll
    instead, which the mtime-based daemon cycle already expects.
    """
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)


def _folder_to_path(folder: str) -> str:
    """Turn a ``file://`` workspace URI into a local path."""
    if not folder:
        return ""
    if folder.startswith("file://"):
        return unquote(urlparse(folder).path)
    return folder


def workspace_project_map(
    workspace_dir: Path = CURSOR_WORKSPACE_DIR,
) -> dict[str, str]:
    """Map composer id to project name using Cursor's workspace stores."""
    mapping: dict[str, str] = {}
    if not workspace_dir.is_dir():
        return mapping

    for entry in sorted(workspace_dir.iterdir()):
        meta = entry / "workspace.json"
        db = entry / "state.vscdb"
        if not (meta.is_file() and db.is_file()):
            continue
        try:
            folder = json.loads(meta.read_text()).get("folder") or ""
        except (OSError, json.JSONDecodeError):
            continue
        local = _folder_to_path(folder)
        if not local:
            continue
        project = resolve_project(local)

        try:
            con = _connect_ro(db)
        except sqlite3.Error:
            continue
        try:
            row = con.execute(
                "SELECT value FROM ItemTable WHERE key = 'composer.composerData'"
            ).fetchone()
        except sqlite3.Error:
            continue
        finally:
            con.close()
        if not row:
            continue

        try:
            composers = json.loads(row[0]).get("allComposers") or []
        except (json.JSONDecodeError, TypeError):
            continue
        for item in composers:
            if isinstance(item, dict) and item.get("composerId"):
                mapping[item["composerId"]] = project
    return mapping


def _project_from_paths(raw_values: list[str]) -> str:
    """Infer a project from file paths mentioned in a composer's bubbles.

    Only three in ten composers are registered in a workspace store, because
    Cursor drops the entry when a workspace is closed or a folder moves, while
    the bubbles live on in global storage forever. Without this fallback the
    bulk of the corpus would file under one meaningless bucket and never
    surface on a project-scoped recall.
    """
    for raw in raw_values:
        for match in _PATH_RE.finditer(raw):
            path = match.group(1)
            # Skip Cursor's own scratch and extension paths.
            if "/Library/Application Support/" in path:
                continue
            # Cursor records both files and directories under "path". Taking
            # .parent unconditionally walked every directory reference up one
            # level, filing a whole folder of separate repos under their shared
            # parent, so a third of threads landed in one "Projects" bucket.
            candidate = Path(path)
            if not candidate.is_dir():
                candidate = candidate.parent
            if not candidate.is_dir():
                continue
            return resolve_project(str(candidate))
    return ""


def _created_at(value) -> float:
    """Cursor writes ISO strings on bubbles and epoch ms on composers."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _tool_result_text(bubble: dict) -> str:
    """Summarize tool activity attached to a bubble.

    Under half of assistant bubbles carry ``text``; the rest are tool calls and
    diffs. Dropping those would keep the question and lose the answer, which is
    the part worth recalling, so the outcome is folded into the message text.

    Cursor records a tool call as a single ``toolFormerData`` object, not the
    ``toolResults`` list the surrounding fields suggest, and its ``result`` is
    a JSON string that has to be decoded a second time.
    """
    parts: list[str] = []

    tool = bubble.get("toolFormerData")
    if isinstance(tool, dict):
        name = tool.get("name") or "tool"
        status = tool.get("status") or ""
        # An errored tool call is often the most informative turn in a
        # session, so failures are labelled rather than skipped.
        header = f"[{name}]" if status in ("completed", "") else f"[{name}: {status}]"

        args = tool.get("rawArgs")
        if isinstance(args, str) and args.strip() and args.strip() != "{}":
            header = f"{header} {args.strip()[:_TOOL_ARGS_LIMIT]}"
        parts.append(header)

        body = _decode_tool_result(tool.get("result"))
        if body:
            parts.append(body[:_TOOL_RESULT_LIMIT])

    for block in bubble.get("codeBlocks") or []:
        if not isinstance(block, dict):
            continue
        uri = block.get("uri") or {}
        path = uri.get("path") if isinstance(uri, dict) else None
        code = block.get("content") or block.get("code") or ""
        if not isinstance(code, str):
            continue
        code = code.strip()
        if not code:
            continue
        label = f"[edit {path}]" if path else "[edit]"
        parts.append(f"{label}\n{code[:_TOOL_RESULT_LIMIT]}")

    return "\n".join(parts).strip()


def _decode_tool_result(result) -> str:
    """Pull readable text out of a tool result payload.

    ``result`` arrives as a JSON string whose useful text sits under one of a
    handful of keys depending on the tool, so the common ones are unwrapped
    and anything unrecognized falls back to its compact JSON form.
    """
    if not result:
        return ""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result.strip()

    if isinstance(result, str):
        return result.strip()
    if not isinstance(result, dict):
        return json.dumps(result, default=str)[:_TOOL_RESULT_LIMIT]

    for key in ("contents", "output", "content", "text", "result", "stdout"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(result, default=str)


def _bubble_text(bubble: dict, *, tool_text: str | None = None) -> str:
    """Message text plus any tool outcome, in reading order."""
    chunks: list[str] = []
    text = bubble.get("text")
    if isinstance(text, str) and text.strip():
        chunks.append(text.strip())
    tools = _tool_result_text(bubble) if tool_text is None else tool_text
    if tools:
        chunks.append(tools)
    return "\n\n".join(chunks).strip()


def _bubble_rows(
    con: sqlite3.Connection, composer_id: str
) -> list[tuple[str, str]]:
    like = f"bubbleId:{composer_id}:%"
    try:
        return con.execute(
            "SELECT key, value FROM cursorDiskKV WHERE key LIKE ? ORDER BY key",
            (like,),
        ).fetchall()
    except sqlite3.Error:
        return []


def _is_signal(text: str, role: str, token_count: int, *, has_tool: bool) -> bool:
    """Decide whether a bubble is worth keeping.

    The shared ``_signal_score`` was tuned on prose: an assistant turn earns its
    place by reading like a decision, a bugfix, or a lesson. Tool turns do not
    talk that way. A terminal result such as "connection refused on port 5432"
    matches none of those cues and scored zero, which dropped 37% of tool turns
    on a real session, and those are precisely the turns worth recalling. Tool
    turns are therefore judged on substance, while plain prose still goes
    through the shared scorer to stay consistent with every other agent.
    """
    if has_tool:
        return token_count >= _MIN_TOOL_TOKENS
    return _signal_score(text, role, token_count) > 0


def parse_session(
    db_path: Path,
    composer_id: str,
    project: str,
    *,
    filter_noise: bool = True,
) -> list[Artifact]:
    """Parse one Cursor composer thread into session_chunk artifacts."""
    if not db_path.is_file():
        return []
    try:
        con = _connect_ro(db_path)
    except sqlite3.Error:
        return []
    try:
        rows = _bubble_rows(con, composer_id)
    finally:
        con.close()

    bubbles: list[tuple[str, dict]] = []
    for key, raw in rows:
        try:
            bubble = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(bubble, dict):
            bubbles.append((key, bubble))

    # Key order is lexicographic on a uuid, which is not chat order. Sort on
    # the timestamp Cursor records, keeping key order as a stable tiebreak for
    # bubbles written inside the same millisecond.
    bubbles.sort(key=lambda kb: (_created_at(kb[1].get("createdAt")), kb[0]))

    arts: list[Artifact] = []
    seen_hashes: set[str] = set()
    for i, (key, bubble) in enumerate(bubbles):
        role = _ROLE_BY_TYPE.get(bubble.get("type"))
        if role is None:
            continue

        tool_text = _tool_result_text(bubble)
        text = _bubble_text(bubble, tool_text=tool_text)
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
        if filter_noise and not _is_signal(
            text, role, token_count, has_tool=bool(tool_text)
        ):
            continue

        bubble_id = bubble.get("bubbleId") or key.rsplit(":", 1)[-1]
        arts.append(Artifact(
            id=f"{composer_id}:{bubble_id}",
            kind="session_chunk",
            project=project,
            source="cursor",
            text=text,
            token_count=token_count,
            created_at=_created_at(bubble.get("createdAt")),
            meta={
                "session_id": composer_id,
                "role": role,
                "ord": i,
                "agent": "cursor",
            },
        ))
    return arts


def scan_cursor_sessions(
    db_path: Path = CURSOR_GLOBAL_DB,
    *,
    workspace_dir: Path = CURSOR_WORKSPACE_DIR,
) -> list[tuple[str, str, float]]:
    """Return (composer_id, project_name, updated_at_epoch) per Cursor thread."""
    results: list[tuple[str, str, float]] = []
    if not db_path.is_file():
        return results

    mapping = workspace_project_map(workspace_dir)

    try:
        con = _connect_ro(db_path)
    except sqlite3.Error:
        return results
    try:
        # One pass over the whole keyspace. Per-composer queries would mean a
        # LIKE scan each over ~143k rows, and the daemon calls this every poll.
        latest: dict[str, float] = {}
        unresolved: dict[str, list[str]] = {}
        try:
            cursor = con.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'"
            )
        except sqlite3.Error:
            return results

        for key, raw in cursor:
            parts = key.split(":")
            if len(parts) < 3:
                continue
            composer_id = parts[1]

            # Test for a path as we stream rather than banking the first few
            # bubbles: a thread often opens with several path-free turns, so a
            # fixed head sample finds nothing and the thread falls to
            # "unknown". Store only the raw values that actually match.
            if composer_id not in mapping:
                samples = unresolved.setdefault(composer_id, [])
                if len(samples) < 5 and _PATH_RE.search(raw):
                    samples.append(raw)

            stamp = 0.0
            try:
                stamp = _created_at(json.loads(raw).get("createdAt"))
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass
            if stamp > latest.get(composer_id, 0.0):
                latest[composer_id] = stamp
    finally:
        con.close()

    for composer_id, mtime in latest.items():
        project = mapping.get(composer_id) or ""
        if not project:
            project = _project_from_paths(unresolved.get(composer_id, []))
        if not project:
            project = "unknown"
        results.append((composer_id, project, mtime))

    results.sort(key=lambda r: r[0])
    return results


def cursor_state_key(composer_id: str) -> str:
    return f"cursor:{composer_id}"
