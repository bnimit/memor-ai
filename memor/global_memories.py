"""Cross-project global memory promotion.

Detects patterns that appear across 3+ projects and promotes them to the
_global scope so they're recalled everywhere. Fully local — no API keys.
"""
from __future__ import annotations
import hashlib
import math
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact, Scope, GLOBAL_PROJECT
from memor.tokencount import count_tokens


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _similarity_matrix(vecs):
    """Row-normalized vectors whose dot products are cosines, as one matrix.

    The pairwise scan is O(n^2) in the number of memories, and at 2,441
    memories that is ~3M pairs. Done as a Python loop it took ~40s -- longer
    than the 30s poll interval, so the daemon never reached idle and sat at
    90% CPU indefinitely. As a single matrix product it is milliseconds.

    Returns None when numpy is unavailable, so the caller keeps its original
    behaviour rather than failing.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    matrix = np.asarray(vecs, dtype=np.float32)
    if matrix.ndim != 2 or matrix.size == 0:
        return None
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # A zero vector has no direction, so it is similar to nothing; dividing by
    # one leaves its dot products at zero rather than producing a nan.
    norms[norms == 0.0] = 1.0
    return matrix / norms


def find_promotion_candidates(
    store: SqliteStore, embedder, *,
    min_projects: int = 3, sim_threshold: float = 0.85,
) -> list[dict]:
    """Find memories that appear in min_projects+ different projects.

    Returns list of dicts: {"text": str, "source_ids": [str], "projects": [str]}
    """
    rows = store.db.execute(
        "SELECT * FROM artifacts WHERE kind='memory' AND active=1 AND project != ?",
        (GLOBAL_PROJECT,)
    ).fetchall()
    if len(rows) < min_projects:
        return []

    memories = [store._row_to_artifact(r) for r in rows]
    vecs = embedder.embed([m.text for m in memories])
    unit = _similarity_matrix(vecs)

    clusters: list[dict] = []
    assigned = set()

    for i, mem in enumerate(memories):
        if i in assigned:
            continue
        cluster = {"text": mem.text, "source_ids": [mem.id], "projects": {mem.project}}
        assigned.add(i)
        # One dot product against every later memory at once, instead of a
        # Python call per pair.
        sims = unit[i + 1:] @ unit[i] if unit is not None else None
        for j in range(i + 1, len(memories)):
            if j in assigned:
                continue
            if memories[j].project in cluster["projects"]:
                continue
            sim = float(sims[j - i - 1]) if sims is not None else _cosine(vecs[i], vecs[j])
            if sim >= sim_threshold:
                cluster["source_ids"].append(memories[j].id)
                cluster["projects"].add(memories[j].project)
                assigned.add(j)
        if len(cluster["projects"]) >= min_projects:
            cluster["projects"] = sorted(cluster["projects"])
            clusters.append(cluster)

    return clusters


def promote_to_global(
    store: SqliteStore, embedder,
    text: str, source_ids: list[str],
) -> str | None:
    """Create a _global memory from text and deactivate source duplicates.

    Returns the new global memory ID, or None if a similar global already exists.
    """
    vec = embedder.embed([text])[0]

    existing = store.search(vec, Scope(project=GLOBAL_PROJECT, kinds=["memory"]), k=1)
    if existing and existing[0][1] >= 0.85:
        return None

    mid = f"mem:global:{hashlib.sha1(text.encode()).hexdigest()[:8]}"
    import time
    art = Artifact(
        id=mid, kind="memory", project=GLOBAL_PROJECT, source="promotion",
        text=text, token_count=max(1, count_tokens(text)), created_at=time.time(),
        meta={"mem_type": "global", "promoted_from": source_ids},
    )
    store.add_artifacts([art], [vec])

    for sid in source_ids:
        store.deactivate(sid, superseded_by=mid)

    return mid


def run_promotion(store: SqliteStore, embedder, *, min_projects: int = 3) -> int:
    """Find and promote cross-project patterns. Returns count promoted."""
    candidates = find_promotion_candidates(
        store, embedder, min_projects=min_projects)
    promoted = 0
    for c in candidates:
        mid = promote_to_global(store, embedder, c["text"], c["source_ids"])
        if mid:
            promoted += 1
    return promoted
