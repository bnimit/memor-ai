from __future__ import annotations
import math
import time
from memor.types import Scope, Hit, RetrievalTrace
from memor.interfaces import Embedder, MemoryStore

EDGE_TYPES = ["fixes", "supersedes", "part_of", "derived_from"]

KIND_WEIGHTS = {
    "memory": 1.3,
    "session_chunk": 1.0,
    "note": 1.1,
}

# Half-life in days: memories older than this get half the recency boost
RECENCY_HALF_LIFE_DAYS = 14

# Reciprocal Rank Fusion constant. Larger = flatter (rank position matters less).
RRF_K = 60


def rrf_fuse(ranked_lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion: combine several ranked id-lists into one score
    map. Each list contributes 1/(k + rank) per item (rank is 1-indexed).
    Items present in more lists, or ranked higher, score higher. The union of
    all lists is scored, so an item found by only one channel still surfaces."""
    scores: dict[str, float] = {}
    for ids in ranked_lists:
        for rank, _id in enumerate(ids, start=1):
            scores[_id] = scores.get(_id, 0.0) + 1.0 / (k + rank)
    return scores


class Retriever:
    def __init__(self, store: MemoryStore, embedder: Embedder, *,
                 k: int = 8, recency_weight: float = 0.25,
                 kind_weight: float = 0.15, quality_weight: float = 0.10,
                 min_similarity: float = 0.0, edge_expand: bool = True,
                 candidate_pool: int = 128, pool_per_kind: int = 64):
        self.store, self.embedder = store, embedder
        self.k, self.edge_expand = k, edge_expand
        self.min_similarity = min_similarity
        # The blend ranks over this many candidates (then cuts to k); pool_per_kind
        # reserves distilled-memory slots so chunks can't crowd them out.
        self.candidate_pool = candidate_pool
        self.pool_per_kind = pool_per_kind
        self.w_sim = 1.0 - recency_weight - kind_weight - quality_weight
        self.w_rec = recency_weight
        self.w_kind = kind_weight
        self.w_qual = quality_weight

    def query(self, text: str, scope: Scope) -> RetrievalTrace:
        t0 = time.perf_counter()
        now = time.time()
        qv = self.embedder.embed([text])[0]
        dense = self.store.search(qv, scope, self.candidate_pool,
                                  pool_per_kind=self.pool_per_kind)

        # Absolute-similarity gate: drop anti-correlated candidates BEFORE
        # fusion/blending. Min-max normalization forces the top hit to a
        # normalized score of 1.0 regardless of its true cosine, so a
        # blended-score threshold cannot reject semantically-irrelevant results.
        # Gating on raw cosine here is the real relevance filter. (Static
        # embeddings put relevant content at >0 and noise at <0, so the default
        # floor is 0.0.) Set min_similarity below -1.0 to disable the gate.
        dense = [(a, sim) for a, sim in dense if sim >= self.min_similarity]

        # Lexical channel: BM25 over the exact text recovers rare identifiers /
        # error strings that the static dense embedding collapses, then RRF fuses
        # the two rankings. It only activates when the dense channel found the
        # query on-topic at all (gated dense is non-empty). Dense cleanly
        # separates on-topic (positive cosine) from off-topic (negative), so this
        # stops a generic multi-word query from OR-matching weak, off-topic terms
        # when nothing in the project is actually relevant.
        lexical = []
        if dense and hasattr(self.store, 'search_lexical'):
            lexical = self.store.search_lexical(text, scope, self.candidate_pool,
                                                pool_per_kind=self.pool_per_kind)

        arts_by_id: dict[str, object] = {}
        sim_by_id: dict[str, float] = {}
        for a, sim in dense:
            arts_by_id[a.id] = a
            sim_by_id[a.id] = sim
        for a, _ in lexical:
            arts_by_id.setdefault(a.id, a)

        candidates = len(arts_by_id)
        hits: dict[str, Hit] = {}

        fused = rrf_fuse([[a.id for a, _ in dense], [a.id for a, _ in lexical]])
        rel_vals = list(fused.values())
        rel_min = min(rel_vals) if rel_vals else 0.0
        rel_range = ((max(rel_vals) - rel_min) if rel_vals else 1.0) or 1.0

        if hasattr(self.store, 'get_quality_scores'):
            quality_scores = self.store.get_quality_scores(list(arts_by_id))
        else:
            quality_scores = {}

        for aid, a in arts_by_id.items():
            norm_rel = (fused.get(aid, 0.0) - rel_min) / rel_range

            age_days = (now - a.created_at) / 86400
            recency = math.exp(-0.693 * age_days / RECENCY_HALF_LIFE_DAYS)

            kind_boost = KIND_WEIGHTS.get(a.kind, 1.0) - 1.0

            quality = quality_scores.get(aid, 0.5)

            score = (self.w_sim * norm_rel + self.w_rec * recency
                     + self.w_kind * kind_boost + self.w_qual * quality)
            hits[aid] = Hit(a, score, {
                "sim": sim_by_id.get(aid, 0.0), "rel": round(norm_rel, 3),
                "recency": round(recency, 3), "kind": a.kind,
                "quality": round(quality, 3), "edge": 0.0,
            })

        if self.edge_expand and arts_by_id:
            seed_ids = list(arts_by_id.keys())
            for nb in self.store.neighbors(seed_ids, EDGE_TYPES, hops=1):
                if nb.id not in hits:
                    hits[nb.id] = Hit(nb, 0.5 * max(h.score for h in hits.values()),
                                      {"sim": 0.0, "rel": 0.0,
                                       "recency": 0.0, "kind": nb.kind, "edge": 1.0})

        ranked = sorted(hits.values(), key=lambda h: h.score, reverse=True)[:self.k]
        return RetrievalTrace(query=text, scope=scope, candidates=candidates,
                              hits=ranked, latency_ms=(time.perf_counter()-t0)*1000)
