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


class Retriever:
    def __init__(self, store: MemoryStore, embedder: Embedder, *,
                 k: int = 8, recency_weight: float = 0.25,
                 kind_weight: float = 0.15, edge_expand: bool = True):
        self.store, self.embedder = store, embedder
        self.k, self.edge_expand = k, edge_expand
        self.w_sim = 1.0 - recency_weight - kind_weight
        self.w_rec = recency_weight
        self.w_kind = kind_weight

    def query(self, text: str, scope: Scope) -> RetrievalTrace:
        t0 = time.perf_counter()
        now = time.time()
        qv = self.embedder.embed([text])[0]
        base = self.store.search(qv, scope, self.k)

        candidates = len(base)
        hits: dict[str, Hit] = {}

        sim_scores = [sim for _, sim in base]
        sim_max = max(sim_scores) if sim_scores else 1.0
        sim_min = min(sim_scores) if sim_scores else 0.0
        sim_range = (sim_max - sim_min) or 1.0

        for a, sim in base:
            norm_sim = (sim - sim_min) / sim_range

            age_days = (now - a.created_at) / 86400
            recency = math.exp(-0.693 * age_days / RECENCY_HALF_LIFE_DAYS)

            kind_boost = KIND_WEIGHTS.get(a.kind, 1.0) - 1.0

            score = self.w_sim * norm_sim + self.w_rec * recency + self.w_kind * kind_boost
            hits[a.id] = Hit(a, score, {
                "sim": sim, "norm_sim": round(norm_sim, 3),
                "recency": round(recency, 3), "kind": a.kind, "edge": 0.0,
            })

        if self.edge_expand and base:
            seed_ids = [a.id for a, _ in base]
            for nb in self.store.neighbors(seed_ids, EDGE_TYPES, hops=1):
                if nb.id not in hits:
                    hits[nb.id] = Hit(nb, 0.5 * max(h.score for h in hits.values()),
                                      {"sim": 0.0, "norm_sim": 0.0,
                                       "recency": 0.0, "kind": nb.kind, "edge": 1.0})

        ranked = sorted(hits.values(), key=lambda h: h.score, reverse=True)[:self.k]
        return RetrievalTrace(query=text, scope=scope, candidates=candidates,
                              hits=ranked, latency_ms=(time.perf_counter()-t0)*1000)
