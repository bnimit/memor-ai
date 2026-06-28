"""Heuristic supersession — local, no LLM. Pure helpers live here; the store
owns persistence and the distiller owns detection wiring."""
from __future__ import annotations

VALIDITY_FLOOR = 0.25

def validity_for(n_active_disputers: int) -> float:
    if n_active_disputers <= 0:
        return 1.0
    return max(VALIDITY_FLOOR, 0.5 ** n_active_disputers)


from memor.types import Scope
from memor.temporal import mem_type_of

BAND_LOW = 0.80
BAND_HIGH = 0.92            # >= is dedup, handled elsewhere
QUALITY_MIN = 0.5
QUALITY_MARGIN = 0.1

# Types that can carry a fact worth contradicting. snippet/session_chunk/global excluded.
FACT_BEARING = {"decision", "bugfix", "lesson", "extract", "memory",
                "note", "research", "page"}


def should_dispute(*, sim: float, new_type: str, old_type: str,
                   new_created: float, old_created: float,
                   new_quality: float, old_quality: float) -> bool:
    if not (BAND_LOW <= sim < BAND_HIGH):
        return False
    if new_type not in FACT_BEARING or old_type not in FACT_BEARING:
        return False
    if not new_created > old_created:
        return False
    if new_quality < QUALITY_MIN:
        return False
    if new_quality < old_quality - QUALITY_MARGIN:
        return False
    return True


def find_disputes(store, embedder, new_art, new_quality: float = 0.5,
                  vec=None) -> list[str]:
    """After new_art is stored, mark older same-topic fact-bearing memories it
    supersedes. Reuses the caller's embedding (`vec`) when provided to avoid a
    redundant embed. Persists disputes + validity. Returns the disputed ids."""
    if vec is None:
        vec = embedder.embed([new_art.text])[0]
    candidates = store.search(vec, Scope(project=new_art.project, kinds=["memory"]), k=10)
    old_ids = [a.id for a, _ in candidates if a.id != new_art.id]
    quals = store.get_quality_scores(old_ids) if old_ids else {}
    disputed: list[str] = []
    for old_art, sim in candidates:
        if old_art.id == new_art.id:
            continue
        if should_dispute(
            sim=sim,
            new_type=mem_type_of(new_art), old_type=mem_type_of(old_art),
            new_created=new_art.created_at, old_created=old_art.created_at,
            new_quality=new_quality, old_quality=quals.get(old_art.id, 0.5),
        ):
            store.add_dispute(old_art.id, new_art.id, new_art.created_at)
            store.recompute_validity(old_art.id)
            disputed.append(old_art.id)
    return disputed
