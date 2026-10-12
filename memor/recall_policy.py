"""Recall inject profiles — default vs strict (prove-recall treatment).

Strict spends less context and prefers distilled memories over session chunks.
Opt-in via ``MEMOR_RECALL_PROFILE=strict`` until prove-recall gates clear.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

PROFILE_ENV = "MEMOR_RECALL_PROFILE"
DEFAULT_PROFILE = "default"
STRICT_PROFILE = "strict"

STRICT_SCORE_FLOOR = 0.25
STRICT_MAX_HITS = 4

CHUNK_KINDS = frozenset({"session_chunk", "snippet"})


@dataclass(frozen=True)
class RecallProfile:
    name: str
    score_floor: float | None = None
    max_hits: int | None = None
    prefer_memories: bool = False
    enable_supersession: bool = False


PROFILES: dict[str, RecallProfile] = {
    DEFAULT_PROFILE: RecallProfile(name=DEFAULT_PROFILE),
    STRICT_PROFILE: RecallProfile(
        name=STRICT_PROFILE,
        score_floor=STRICT_SCORE_FLOOR,
        max_hits=STRICT_MAX_HITS,
        prefer_memories=True,
        enable_supersession=True,
    ),
}


def active_profile_name() -> str:
    raw = (os.environ.get(PROFILE_ENV) or DEFAULT_PROFILE).strip().lower()
    return raw if raw in PROFILES else DEFAULT_PROFILE


def active_profile() -> RecallProfile:
    return PROFILES[active_profile_name()]


def apply_recall_policy(hits: list[Any], *, threshold: float = 0.0) -> tuple[list[Any], dict]:
    """Filter ranked hits according to ``MEMOR_RECALL_PROFILE``.

    ``hits`` are objects with ``.score`` and ``.artifact.kind`` (Retriever hits).
    Returns ``(filtered_hits, meta)`` where meta is safe to log in evidence.
    """
    profile = active_profile()
    meta: dict = {
        "profile": profile.name,
        "input_n": len(hits),
        "score_floor_applied": None,
        "dropped_low_score": 0,
        "dropped_chunks_for_memories": 0,
        "trimmed_to_max_hits": 0,
    }
    out = list(hits)

    floor = profile.score_floor
    effective_floor = threshold
    if floor is not None:
        effective_floor = max(threshold, floor)
    meta["score_floor_applied"] = effective_floor
    if effective_floor > 0:
        kept = [h for h in out if getattr(h, "score", 0) >= effective_floor]
        meta["dropped_low_score"] = len(out) - len(kept)
        out = kept

    if profile.prefer_memories and out:
        has_memory = any(getattr(h.artifact, "kind", "") == "memory" for h in out)
        if has_memory:
            kept = [
                h for h in out
                if getattr(h.artifact, "kind", "") not in CHUNK_KINDS
            ]
            meta["dropped_chunks_for_memories"] = len(out) - len(kept)
            out = kept

    if profile.max_hits is not None and len(out) > profile.max_hits:
        meta["trimmed_to_max_hits"] = len(out) - profile.max_hits
        out = out[: profile.max_hits]

    meta["output_n"] = len(out)
    return out, meta


def maybe_enable_supersession_for_profile() -> bool:
    """Return True if the active profile wants supersession for this process.

    Callers that own the env (prove-recall arms) may set ``MEMOR_SUPERSESSION``;
    hot-path recall does not mutate env — Retriever already reads the flag.
    """
    return active_profile().enable_supersession
