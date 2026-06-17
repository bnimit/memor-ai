"""Ingest-time reaffirmation: when new session content re-observes an existing
memory, stamp the memory's last_reaffirmed so its recall recency stays fresh.

Guarded by the distiller's replacement-cue regex — a chunk that looks like a
contradiction ("switched from X", "no longer …") is NOT a reaffirmation; it's
left to the existing cue-based supersession path.
"""
from __future__ import annotations
from memor.types import Scope
from memor.distill.distiller import _REPLACEMENT_RE

# Match the supersede threshold: a reaffirmation must be a genuinely close
# re-observation, not merely same-topic. (Premise check: 0.6 reaffirms 90% of
# memories — non-discriminative; 0.8 gives a meaningful split.)
REAFFIRM_SIM_THRESHOLD = 0.80
# Fetch depth per chunk: memories are ~10% of the corpus, so look past the
# nearby chunks to find any memory the chunk re-observes.
REAFFIRM_FETCH_K = 50


def reaffirm_from_chunks(store, chunks, vectors) -> int:
    """For each new session_chunk, reaffirm active memories it closely
    re-observes (cosine >= threshold, same project), unless the chunk carries a
    replacement cue. Batched: one reaffirm() write per memory at the latest
    matching chunk time. Returns the number of memories reaffirmed."""
    latest: dict[str, float] = {}
    for art, vec in zip(chunks, vectors):
        if art.kind != "session_chunk":
            continue
        if _REPLACEMENT_RE.search(art.text or ""):
            continue  # potential contradiction → supersession, not reaffirmation
        for m, sim in store.search(vec, Scope(project=art.project), REAFFIRM_FETCH_K):
            if m.kind == "memory" and sim >= REAFFIRM_SIM_THRESHOLD:
                if art.created_at > latest.get(m.id, 0.0):
                    latest[m.id] = art.created_at
    by_ts: dict[float, list[str]] = {}
    for mid, ts in latest.items():
        by_ts.setdefault(ts, []).append(mid)
    for ts, mids in by_ts.items():
        store.reaffirm(mids, ts)
    return len(latest)
