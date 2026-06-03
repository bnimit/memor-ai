from __future__ import annotations
import time
from memor.types import Scope, Hit, RetrievalTrace
from memor.interfaces import Embedder, MemoryStore

EDGE_TYPES = ["fixes", "supersedes", "part_of", "derived_from"]

class Retriever:
    def __init__(self, store: MemoryStore, embedder: Embedder, *,
                 k: int = 8, recency_weight: float = 0.2, edge_expand: bool = True):
        self.store, self.embedder = store, embedder
        self.k, self.recency_weight, self.edge_expand = k, recency_weight, edge_expand

    def query(self, text: str, scope: Scope) -> RetrievalTrace:
        t0 = time.perf_counter()
        qv = self.embedder.embed([text])[0]
        base = self.store.search(qv, scope, self.k)          # [(Artifact, sim)]
        candidates = len(base)
        hits: dict[str, Hit] = {}
        # recency normalization over the candidate window
        times = [a.created_at for a, _ in base] or [0.0]
        tmin, tmax = min(times), max(times)
        span = (tmax - tmin) or 1.0
        for a, sim in base:
            rec = (a.created_at - tmin) / span
            score = (1 - self.recency_weight) * sim + self.recency_weight * rec
            hits[a.id] = Hit(a, score, {"sim": sim, "recency": rec, "edge": 0.0})
        # 1-hop edge expansion: linked artifacts inherit a discounted score
        if self.edge_expand and base:
            seed_ids = [a.id for a, _ in base]
            for nb in self.store.neighbors(seed_ids, EDGE_TYPES, hops=1):
                if nb.id not in hits:
                    hits[nb.id] = Hit(nb, 0.5 * max(h.score for h in hits.values()),
                                      {"sim": 0.0, "recency": 0.0, "edge": 1.0})
        ranked = sorted(hits.values(), key=lambda h: h.score, reverse=True)[:self.k]
        return RetrievalTrace(query=text, scope=scope, candidates=candidates,
                              hits=ranked, latency_ms=(time.perf_counter()-t0)*1000)
