"""Runtime fail-open shim for proxy compression."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from memor.proxy.pipeline import run_pipeline
from memor.store.sqlite_store import SqliteStore
from memor.tokencount import count_tokens

logger = logging.getLogger(__name__)


@dataclass
class CompressorState:
    mode: Literal["compress", "passthrough"] = "compress"
    compressor_ready: bool = True


compressor_state = CompressorState()


@dataclass
class ShimResult:
    """Body and ledger fields after compression attempt or shim passthrough."""

    body: dict
    tokens_before: int
    tokens_after: int
    content_types: dict
    passthrough: bool


def _passthrough_shim(original_body: dict) -> ShimResult:
    tokens = count_tokens(json.dumps(original_body, separators=(",", ":")))
    compressor_state.mode = "passthrough"
    compressor_state.compressor_ready = False
    return ShimResult(
        body=original_body,
        tokens_before=tokens,
        tokens_after=tokens,
        content_types={},
        passthrough=True,
    )


def prepare_request_body(
    provider: str,
    original_body: dict,
    store: SqliteStore,
    *,
    db_path: str,
    embedder,
    project: str,
    agent: str = "unknown",
    session_id: str = "",
) -> ShimResult:
    """Try run_pipeline + inject_memory; on failure use original body (shim)."""
    if not compressor_state.compressor_ready:
        return _passthrough_shim(original_body)

    try:
        result = run_pipeline(provider, original_body, store)
        from memor.proxy.memory import inject_memory

        body = inject_memory(
            provider,
            result.body,
            project=project,
            db_path=db_path,
            embedder=embedder,
            store=store,
            agent=agent,
            session_id=session_id,
        )
        compressor_state.mode = "compress"
        compressor_state.compressor_ready = True
        return ShimResult(
            body=body,
            tokens_before=result.tokens_before,
            tokens_after=result.tokens_after,
            content_types=result.content_types,
            passthrough=result.passthrough,
        )
    except Exception:
        logger.warning("compressor failed; forwarding original body (shim)", exc_info=True)
        return _passthrough_shim(original_body)
