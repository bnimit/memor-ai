from __future__ import annotations
from dataclasses import dataclass

@dataclass
class CompressResult:
    text: str
    content_type: str
    tokens_before: int
    tokens_after: int
    passthrough: bool
    ccr_id: str | None = None
