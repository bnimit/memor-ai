from __future__ import annotations
from dataclasses import dataclass
from uuid import uuid4
import time
from memor.compress import compress_text
from memor.proxy.adapters import extract_latest_tool_payloads, apply_payload_text
from memor.store.sqlite_store import SqliteStore

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
    """
    # Extract latest tool payloads
    payloads = extract_latest_tool_payloads(provider, body)
    
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
        result = compress_text(payload.text)
        
        total_tokens_before += result.tokens_before
        
        if result.passthrough:
            # Compression failed, leave unchanged
            compressed_payloads.append((payload.path, payload.text, None))
            total_tokens_after += result.tokens_before
        else:
            # Compression succeeded
            success_count += 1
            
            # Generate CCR ID and store original
            ccr_id = uuid4().hex
            store.ccr_put(ccr_id, payload.text, result.content_type, time.time())
            ccr_ids.append(ccr_id)
            
            # Prepend marker line to compressed text
            marked_text = f"[memor:ccr:{ccr_id}]\n{result.text}"
            compressed_payloads.append((payload.path, marked_text, result.content_type))
            
            total_tokens_after += result.tokens_after
            
            # Track content types
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
