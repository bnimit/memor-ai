"""Soft temporal disputes — detect, persist, recompute validity.

Hard ``active=0`` is reserved for exact dedup (true cosine ≥ 0.92). Semantic
updates write rows here and demote via ``validity`` at recall time when
``MEMOR_SUPERSESSION`` is on.
"""
from __future__ import annotations

import os
import time
from typing import Iterable

from memor.retrieve.similarity import (
    DISPUTE_COSINE_HI,
    DISPUTE_COSINE_LO,
    cosine_in_dispute_band,
    stored_sim_to_cosine,
)
from memor.types import Scope

FACT_BEARING_TYPES = frozenset({
    "decision", "bugfix", "lesson", "extract", "memory", "note", "research", "page",
})
EXCLUDED_KINDS = frozenset({"snippet", "session_chunk"})

VALIDITY_FLOOR = 0.25
QUALITY_SLACK = 0.1
MIN_DISPUTER_QUALITY = 0.5
AFFIRMATION_DORMANT_AT = 2
DISPUTE_KNN_K = 8


def supersession_action_enabled() -> bool:
    return os.environ.get("MEMOR_SUPERSESSION", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def type_halflife_enabled() -> bool:
    return os.environ.get("MEMOR_TYPE_HALFLIFE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


HALF_LIFE_DAYS: dict[str, float] = {
    "decision": 90,
    "global": 180,
    "bugfix": 60,
    "snippet": 60,
    "lesson": 45,
    "extract": 21,
    "research": 120,
    "page": 120,
    "note": 90,
    "memory": 45,
    "session_chunk": 14,
}
DEFAULT_HALF_LIFE_DAYS = 14.0


def half_life_days(artifact) -> float:
    """Per-type half-life; falls back to kind, then 14d."""
    meta = getattr(artifact, "meta", None) or {}
    mem_type = meta.get("mem_type") if isinstance(meta, dict) else None
    if mem_type and mem_type in HALF_LIFE_DAYS:
        return HALF_LIFE_DAYS[mem_type]
    kind = getattr(artifact, "kind", None)
    if kind and kind in HALF_LIFE_DAYS:
        return HALF_LIFE_DAYS[kind]
    return DEFAULT_HALF_LIFE_DAYS


def _mem_type(artifact) -> str | None:
    meta = getattr(artifact, "meta", None) or {}
    if isinstance(meta, dict):
        return meta.get("mem_type")
    return None


def is_fact_bearing(artifact) -> bool:
    kind = getattr(artifact, "kind", None)
    if kind in EXCLUDED_KINDS:
        return False
    mt = _mem_type(artifact)
    if mt == "snippet":
        return False
    if mt in FACT_BEARING_TYPES:
        return True
    return kind in {"memory", "note", "research", "page"}


def validity_from_active_count(n: int) -> float:
    if n <= 0:
        return 1.0
    return max(VALIDITY_FLOOR, 0.5 ** n)


def find_and_record_disputes(store, embedder, memory_id: str, *,
                            project: str, knn_k: int = DISPUTE_KNN_K) -> list[str]:
    """Detect soft disputes for a newly stored memory; return disputed ids."""
    row = store.db.execute(
        "SELECT * FROM artifacts WHERE id=? AND active=1", (memory_id,)
    ).fetchone()
    if row is None:
        return []
    m = store._row_to_artifact(row)
    if not is_fact_bearing(m):
        return []

    vec = embedder.embed([m.text])[0]
    neighbors = store.search(
        vec, Scope(project=project, kinds=["memory"]), k=knn_k,
    )
    m_quality = store.get_quality_score(memory_id)
    disputed: list[str] = []
    for other, stored_sim in neighbors:
        if other.id == memory_id:
            continue
        if not is_fact_bearing(other):
            continue
        if other.created_at >= m.created_at:
            continue
        if not cosine_in_dispute_band(stored_sim):
            continue
        o_quality = store.get_quality_score(other.id)
        if m_quality < MIN_DISPUTER_QUALITY:
            continue
        if m_quality < o_quality - QUALITY_SLACK:
            continue
        if store.add_dispute(disputed_id=other.id, disputer_id=memory_id):
            store.recompute_validity(other.id)
            disputed.append(other.id)
    return disputed


def drop_disputed_when_disputer_present(candidate_ids: Iterable[str],
                                        store) -> set[str]:
    """Ids to remove: disputed O when any active disputer is also a candidate."""
    ids = list(candidate_ids)
    if not ids or not supersession_action_enabled():
        return set()
    present = set(ids)
    drop: set[str] = set()
    for oid in ids:
        for mid in store.active_disputers(oid):
            if mid in present:
                drop.add(oid)
                break
    return drop


def backfill_disputes(store, embedder, *, project: str | None = None) -> dict:
    """KNN dispute scan over active memories. Idempotent via INSERT OR IGNORE."""
    clauses = ["kind='memory'", "active=1"]
    params: list = []
    if project:
        clauses.append("project=?")
        params.append(project)
    rows = store.db.execute(
        f"SELECT id, project FROM artifacts WHERE {' AND '.join(clauses)} "
        f"ORDER BY created_at ASC",
        params,
    ).fetchall()
    n_disputed = 0
    for r in rows:
        found = find_and_record_disputes(
            store, embedder, r["id"], project=r["project"],
        )
        n_disputed += len(found)
    store.db.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('disputes_backfilled', '1')"
    )
    store.db.commit()
    return {"memories_scanned": len(rows), "disputes_recorded": n_disputed}
