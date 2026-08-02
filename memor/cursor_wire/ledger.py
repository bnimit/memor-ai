"""Record Cursor wire compression savings into the Memor proxy_savings ledger."""
from __future__ import annotations

import time
from pathlib import Path

from memor.cursor_wire.bidi_compress import CompressRewriteResult
from memor.store.sqlite_store import SqliteStore

AGENT = "cursor-wire"
PROVIDER = "cursor-bidi"


def default_db_path() -> Path:
    return Path.home() / ".memor" / "memor.db"


def record_wire_savings(
    result: CompressRewriteResult,
    *,
    store: SqliteStore | None = None,
    db_path: str | Path | None = None,
    session_id: str | None = None,
) -> int | None:
    """Insert a proxy_savings row for a compressed BidiAppend frame.

    Returns the row id, or None when there is nothing useful to record.
    """
    # Only attribute real wire savings on the dashboard (skip heartbeats / no-ops).
    if not result.modified or result.tokens_before <= 0:
        return None

    own_store = store is None
    if store is None:
        from memor.store.sqlite_store import read_dim

        path = Path(db_path) if db_path else default_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        store = SqliteStore(str(path), dim=read_dim(str(path), 256))

    try:
        return store.record_proxy_savings(
            {
                "timestamp": time.time(),
                "agent": AGENT,
                "provider": PROVIDER,
                "session_id": session_id,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "content_types": result.content_types,
                "passthrough": 0,
                "upstream_input_tokens": None,
                "upstream_cache_read_tokens": None,
                "upstream_output_tokens": None,
            }
        )
    finally:
        if own_store:
            try:
                store.db.close()
            except Exception:
                pass
