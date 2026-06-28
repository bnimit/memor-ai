"""Per-type temporal decay — shared by recall-time recency and daemon quality
decay. Single source of truth for half-lives so the two never drift."""
from __future__ import annotations

DEFAULT_HALF_LIFE_DAYS = 14

# Half-life in days, keyed on mem_type (preferred) else artifact kind.
HALF_LIFE_DAYS = {
    "decision": 90,
    "global": 180,
    "bugfix": 60,
    "snippet": 60,
    "lesson": 45,
    "extract": 21,
    "research": 120,
    "page": 120,
    "note": 90,
    "memory": 45,          # distilled but unclassified (no mem_type)
    "session_chunk": 14,
}


def mem_type_of(art) -> str:
    """The classification we decay/dispute on: mem_type if distilled, else kind."""
    return art.meta.get("mem_type") or art.kind


def half_life_days(mem_type_or_kind: str) -> int:
    return HALF_LIFE_DAYS.get(mem_type_or_kind, DEFAULT_HALF_LIFE_DAYS)
