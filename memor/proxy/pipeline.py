from __future__ import annotations
from dataclasses import dataclass
from hashlib import blake2b
import time
from memor.compress import compress_text
from memor.compress.code import compress_code, compressible_language
from memor.compress.types import CompressResult
from memor.proxy.adapters import (
    apply_payload_text,
    extract_all_tool_payloads,
    extract_latest_tool_payloads,
)
from memor.store.sqlite_store import SqliteStore
from memor.tokencount import count_tokens

# CCR eviction is cheap but not free; amortise it across requests.
_EVICT_INTERVAL_SECONDS = 300.0
_last_evict_at = 0.0

def _maybe_evict(store: SqliteStore) -> None:
    """Run CCR eviction at most once per _EVICT_INTERVAL_SECONDS. Best effort."""
    global _last_evict_at
    now = time.time()
    if now - _last_evict_at < _EVICT_INTERVAL_SECONDS:
        return
    _last_evict_at = now
    try:
        from memor.config import ccr_ttl_seconds, ccr_max_bytes
        store.ccr_evict(ccr_ttl_seconds(), ccr_max_bytes())
    except Exception:
        pass

#: Marker id length, matching the previous uuid4().hex so marker width — and
#: therefore token count — is unchanged.
_CCR_ID_LEN = 32


def ccr_id_for(text: str) -> str:
    """Content-addressed id for a payload.

    Previously ``uuid4().hex``, which meant compressing identical content twice
    produced different marker text. Any strategy that rewrites a payload
    appearing in more than one request — notably compressing older turns — would
    then change the prompt prefix on every call and miss the cache every time,
    costing far more than the compression saves. Hashing the content makes the
    rewrite stable, so the cache re-forms once on the shorter prefix.
    """
    return blake2b(text.encode("utf-8", "replace"), digest_size=16).hexdigest()[
        :_CCR_ID_LEN
    ]


#: Prefix stamped on a payload this pipeline has already rewritten.
CCR_MARKER_PREFIX = "[memor:ccr:"


def already_compressed(text: str) -> bool:
    """True when this payload is one we rewrote in an earlier request.

    Once older turns are in scope, a payload compressed in request N reappears
    in request N+1 carrying its marker. Re-measuring it double-counts: the
    already-shrunk text lands in ``tokens_before`` and yields nothing, so the
    realized-savings rate falls the more successfully the compressor works.
    """
    return text.lstrip().startswith(CCR_MARKER_PREFIX)


def _compress_payload(payload, *, skeleton_ok: bool) -> CompressResult:
    """Compress one payload, skeletonizing code the agent has moved past.

    The newest read of a file is left byte-exact: it is the one an agent is
    most likely about to edit, and editing against elided lines is the failure
    this whole design exists to avoid. Older reads are fair game — the agent has
    moved on, and they are what the trajectory resends on every subsequent step.
    """
    if skeleton_ok and not payload.is_latest_for_file:
        language = compressible_language(payload.file_path)
        if language:
            skeleton = compress_code(
                payload.text, language=language, file_path=payload.file_path
            )
            if skeleton != payload.text:
                return CompressResult(
                    text=skeleton,
                    content_type=f"code:{language}",
                    tokens_before=count_tokens(payload.text),
                    tokens_after=count_tokens(skeleton),
                    passthrough=False,
                )
    return compress_text(payload.text, file_path=payload.file_path)


@dataclass
class PipelineResult:
    """Result of running the compression pipeline."""
    body: dict
    tokens_before: int
    tokens_after: int
    content_types: dict
    passthrough: bool
    ccr_ids: list[str]

def run_pipeline(provider: str, body: dict, store: SqliteStore) -> PipelineResult:
    """Run the compression pipeline on latest-turn tool payloads.
    
    Compresses only the latest turn's tool payloads, leaving earlier turns
    unchanged for cache safety. On compression failure per payload, leaves
    that payload unchanged. Sets passthrough=True only if all payloads failed
    or none were compressed.

    Token counts include the CCR marker line, and a payload is left verbatim
    (no marker, no CCR blob) whenever the marked-up result would not be smaller
    than the original.
    """
    _maybe_evict(store)

    # Older payloads are where the tokens are: the whole trajectory is resent on
    # every step, so a file dumped early is re-read on each one. Opt-in until
    # measured, because it changes what the model sees.
    from memor.config import is_compress_older_turns

    skeleton_ok = is_compress_older_turns()
    payloads = (
        extract_all_tool_payloads(provider, body)
        if skeleton_ok
        else extract_latest_tool_payloads(provider, body)
    )

    if not payloads:
        # No payloads to compress
        return PipelineResult(
            body=body,
            tokens_before=0,
            tokens_after=0,
            content_types={},
            passthrough=True,
            ccr_ids=[]
        )
    
    # Compress each payload
    compressed_payloads = []
    ccr_ids = []
    content_type_counts = {}
    total_tokens_before = 0
    total_tokens_after = 0
    success_count = 0
    
    for payload in payloads:
        # Our own earlier output. Leave it alone and keep it out of the
        # denominator, or the rate drops the better the compressor does.
        if already_compressed(payload.text):
            continue

        result = _compress_payload(payload, skeleton_ok=skeleton_ok)

        total_tokens_before += result.tokens_before
        
        # Compression failed or was a no-op: nothing to reference, nothing to save.
        if result.passthrough or result.text == payload.text:
            compressed_payloads.append((payload.path, payload.text, None))
            total_tokens_after += result.tokens_before
            continue
        
        ccr_id = ccr_id_for(payload.text)
        marked_text = f"[memor:ccr:{ccr_id}]\n{result.text}"
        marked_tokens = count_tokens(marked_text)
        
        # The marker plus retrieval affordance has to pay for itself.
        if marked_tokens >= result.tokens_before:
            compressed_payloads.append((payload.path, payload.text, None))
            total_tokens_after += result.tokens_before
            continue
        
        # The marker is a promise that the original can be fetched back through
        # the retrieve MCP tool. If the blob cannot be stored, do not make the
        # promise — leave the payload verbatim rather than hand out a reference
        # to nothing. Never fatal: a storage problem must not discard the whole
        # request's compression.
        try:
            store.ccr_put(ccr_id, payload.text, result.content_type, time.time())
        except Exception:
            compressed_payloads.append((payload.path, payload.text, None))
            total_tokens_after += result.tokens_before
            continue

        success_count += 1
        ccr_ids.append(ccr_id)
        compressed_payloads.append((payload.path, marked_text, result.content_type))
        total_tokens_after += marked_tokens
        
        ct = result.content_type
        content_type_counts[ct] = content_type_counts.get(ct, 0) + 1
    
    # Apply compressed payloads to body
    updated_body = body
    for path, new_text, content_type in compressed_payloads:
        updated_body = apply_payload_text(updated_body, path, new_text)
    
    # Determine passthrough: True if all failed or none compressed
    passthrough = (success_count == 0)
    
    return PipelineResult(
        body=updated_body,
        tokens_before=total_tokens_before,
        tokens_after=total_tokens_after,
        content_types=content_type_counts,
        passthrough=passthrough,
        ccr_ids=ccr_ids
    )
