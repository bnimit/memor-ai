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
    #: Which arm of the holdout experiment this request landed in, or None when
    #: the experiment is off. None must not be read as "compressed": rows from
    #: before the experiment existed are not evidence about it.
    experiment_arm: str | None = None


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
        from memor.proxy.experiment import (
            ARM_CONTROL, ARM_TREATMENT, assign_arm, is_enabled,
        )

        experimenting = is_enabled()
        held_out = (
            (lambda text: assign_arm(text) == ARM_CONTROL)
            if experimenting else None
        )
        result = run_pipeline(provider, original_body, store, held_out=held_out)
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

        # A `[memor:ccr:...]` marker tells the model an original exists. Until
        # now nothing on this path declared the tool that fetches it, so the
        # promise depended on an MCP server the user may never have installed.
        from memor.proxy.pipeline import CCR_MARKER_PREFIX
        from memor.proxy.retrieve_tool import body_has_marker, inject_retrieve_tool

        body = inject_retrieve_tool(
            provider, body,
            has_marker=bool(result.ccr_ids)
            or body_has_marker(body, CCR_MARKER_PREFIX),
        )

        compressor_state.mode = "compress"
        compressor_state.compressor_ready = True
        arm = None
        if experimenting:
            # A request is a control only when the holdout actually withheld
            # something. One that had nothing to compress anyway is not
            # evidence either way, and labelling it would pad the control arm
            # with requests the treatment could never have changed.
            arm = ARM_CONTROL if result.holdout_payloads else ARM_TREATMENT
        return ShimResult(
            body=body,
            tokens_before=result.tokens_before,
            tokens_after=result.tokens_after,
            content_types=result.content_types,
            passthrough=result.passthrough,
            experiment_arm=arm,
        )
    except Exception:
        logger.warning("compressor failed; forwarding original body (shim)", exc_info=True)
        return _passthrough_shim(original_body)
