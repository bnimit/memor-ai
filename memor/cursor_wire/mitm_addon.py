"""mitmproxy addon: compress Cursor BidiAppend tool payloads; stream RunSSE.

Launch via:
  memor cursor-wire-mitm
or:
  mitmweb -s $(python -c 'import memor.cursor_wire.mitm_addon as m; print(m.__file__)') \\
    --mode regular@8080 --web-port 8081 --set block_global=false --set stream_large_bodies=1
"""
from __future__ import annotations

import os
from pathlib import Path

from memor.cursor_wire.bidi_compress import rewrite_bidi_append_body
from memor.cursor_wire.ledger import record_wire_savings

try:
    from mitmproxy import ctx, http
except ImportError:  # pragma: no cover - optional dependency
    ctx = None  # type: ignore
    http = None  # type: ignore

CURSOR_HOST_SUFFIXES = (
    ".cursor.sh",
    ".cursorapi.com",
    ".cursor.com",
)

_STREAM_RESPONSE_MARKERS = (
    "/agent.v1.AgentService/RunSSE",
    "text/event-stream",
    "application/connect+proto",
)

_BIDI_PATH = "/aiserver.v1.BidiService/BidiAppend"


def _log(msg: str) -> None:
    if ctx is not None:
        ctx.log.info(f"[memor-cursor-wire] {msg}")
    else:
        print(f"[memor-cursor-wire] {msg}")


def _host_matches(host: str) -> bool:
    host = (host or "").lower()
    return any(host == suffix[1:] or host.endswith(suffix) for suffix in CURSOR_HOST_SUFFIXES)


def _should_stream_response(flow) -> bool:
    path = flow.request.path or ""
    ctype = (flow.response.headers.get("content-type") or "").lower()
    if any(marker in path for marker in _STREAM_RESPONSE_MARKERS):
        return True
    return any(marker in ctype for marker in _STREAM_RESPONSE_MARKERS[1:])


def _db_path() -> str:
    return os.environ.get("MEMOR_DB", str(Path.home() / ".memor" / "memor.db"))


class MemorCursorWireAddon:
    """Compress BidiAppend request bodies; never buffer RunSSE responses."""

    def requestheaders(self, flow) -> None:
        if not _host_matches(flow.request.host or ""):
            return
        path = flow.request.path or ""
        if _BIDI_PATH not in path:
            return
        # Force buffering so we can rewrite before forward (stream_large_bodies=1
        # would otherwise leave raw_content empty for chunked bodies).
        flow.request.stream = False

    def request(self, flow) -> None:
        if not _host_matches(flow.request.host or ""):
            return
        path = flow.request.path or ""
        if _BIDI_PATH not in path:
            return

        raw = flow.request.raw_content or flow.request.content or b""
        if not raw:
            return

        try:
            result = rewrite_bidi_append_body(raw)
        except Exception as exc:  # fail open
            _log(f"rewrite error (passthrough): {exc}")
            return

        if result.modified and result.body != raw:
            flow.request.content = result.body
            # Drop content-length so mitmproxy recalculates after rewrite.
            if "content-length" in flow.request.headers:
                del flow.request.headers["content-length"]
            _log(
                f"compressed {result.message_kind}: "
                f"{result.tokens_before}→{result.tokens_after} tokens "
                f"({len(raw)}→{len(result.body)} bytes)"
            )
        elif result.message_kind:
            _log(f"passthrough {result.message_kind} ({len(raw)} bytes)")

        try:
            record_wire_savings(result, db_path=_db_path())
        except Exception as exc:
            _log(f"ledger error: {exc}")

    def responseheaders(self, flow) -> None:
        if not _host_matches(flow.request.host or ""):
            return
        if flow.response is None:
            return
        if _should_stream_response(flow):
            flow.response.stream = True


addons = [MemorCursorWireAddon()]
