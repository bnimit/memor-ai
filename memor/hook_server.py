from __future__ import annotations
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

SOCK_PATH = Path.home() / ".memor" / "hook.sock"
PID_PATH = Path.home() / ".memor" / "hook.pid"
DEFAULT_DB = str(Path.home() / ".memor" / "memor.db")
IDLE_TIMEOUT_S = 600

_embedder = None
_last_activity = 0.0

_UNSET = object()  # sentinel for "auto-discover embedder"


def _get_embedder():
    global _embedder
    if _embedder is not None:
        return _embedder
    from memor.embed.local import LocalEmbedder
    _embedder = LocalEmbedder()
    return _embedder


def handle_request(req: dict, *, db_path: str = DEFAULT_DB,
                   embedder=_UNSET) -> dict:
    """Process a recall request and return the hook JSON response.
    This is the core logic, used by both the socket server and inline fallback.

    Pass embedder=None to explicitly indicate no embedder is available (returns
    the no_embedder status message). Omit embedder (default) to auto-discover."""
    from memor.recall import recall, _status_message
    from memor.project import resolve_project

    cwd = req.get("cwd", "")
    project = resolve_project(cwd) if cwd else "unknown"
    query = req.get("prompt", "")
    session_id = req.get("session_id", "")

    if embedder is _UNSET:
        embedder = _get_embedder()
    if embedder is None:
        msg = _status_message("no_embedder", project, 0, 0, 0.0)
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": f"---\n{msg}",
            }
        }

    result = recall(query, project, db_path, embedder=embedder, k=8, threshold=0.3)

    # Log the recall event
    if Path(db_path).exists():
        try:
            from memor.store.sqlite_store import SqliteStore
            store = SqliteStore(db_path, dim=embedder.dim)
            store.log_recall(
                project=project, query_preview=query[:100],
                hits_count=result.hits_count, top_score=result.top_score,
                tokens_injected=result.tokens_injected, latency_ms=result.latency_ms,
                status=result.status, session_id=session_id)
        except Exception:
            pass

    additional_context = result.formatted_context or f"---\n{result.status_message}"
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        }
    }


async def _handle_client(reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter) -> None:
    global _last_activity
    _last_activity = time.time()
    try:
        data = await asyncio.wait_for(reader.read(1_000_000), timeout=10)
        req = json.loads(data.decode())
        resp = handle_request(req)
        writer.write(json.dumps(resp).encode())
        await writer.drain()
    except Exception as e:
        err = json.dumps({"error": str(e)})
        writer.write(err.encode())
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def _idle_watchdog():
    global _last_activity
    while True:
        await asyncio.sleep(60)
        if time.time() - _last_activity > IDLE_TIMEOUT_S:
            _cleanup()
            os._exit(0)


def _cleanup():
    if SOCK_PATH.exists():
        SOCK_PATH.unlink()
    if PID_PATH.exists():
        PID_PATH.unlink()


async def serve(sock_path: str = str(SOCK_PATH)) -> None:
    global _last_activity
    _last_activity = time.time()
    p = Path(sock_path)
    if p.exists():
        p.unlink()
    p.parent.mkdir(parents=True, exist_ok=True)

    _get_embedder()

    server = await asyncio.start_unix_server(_handle_client, path=sock_path)
    PID_PATH.write_text(str(os.getpid()))
    asyncio.create_task(_idle_watchdog())

    async with server:
        await server.serve_forever()


def main():
    signal.signal(signal.SIGTERM, lambda *_: (_cleanup(), sys.exit(0)))
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        _cleanup()


if __name__ == "__main__":
    main()
