"""FlashRank latency go/no-go. Reranking runs on the per-prompt hot path, so it
must fit the recall budget (~15ms target for the warm sidecar). Time the
smallest FlashRank cross-encoder reranking real candidate sets of various sizes.
"""
import os, time
import numpy as np
from flashrank import Ranker, RerankRequest
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.types import Scope

DB = os.path.expanduser("~/.memor/memor.db")
TRUNC = 600  # production truncates injected text to 600 chars
e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)

# real recent queries from the log
qrows = s.db.execute("""SELECT DISTINCT query_preview FROM recall_log
    WHERE length(query_preview) > 20 ORDER BY timestamp DESC LIMIT 20""").fetchall()
queries = [r["query_preview"] for r in qrows]

# Only TinyBERT-L-2 (~4MB) — MiniLM-L-12 was ~350ms+, dead on arrival.
for model in ["ms-marco-TinyBERT-L-2-v2"]:
    print(f"\n===== model: {model} (CLEAN run) =====", flush=True)
    ranker = Ranker(model_name=model)
    # warm up (loads model)
    ranker.rerank(RerankRequest(query="warmup", passages=[{"id": "1", "text": "warm up text"}]))
    for N in (10, 20, 30, 40):
        lat = []
        for q in queries:
            qv = e.embed([q])[0]
            cands = s.search(qv, Scope(project=None), N)
            passages = [{"id": a.id, "text": a.text[:TRUNC]} for a, _ in cands]
            if len(passages) < 2:
                continue
            t0 = time.perf_counter()
            ranker.rerank(RerankRequest(query=q, passages=passages))
            lat.append((time.perf_counter() - t0) * 1000)
        lat = np.array(lat)
        print(f"  N={N:>2} candidates:  p50={np.percentile(lat,50):6.1f}ms  "
              f"p95={np.percentile(lat,95):6.1f}ms  max={lat.max():6.1f}ms  (n={len(lat)})",
              flush=True)

print("\nBudget reference: warm-sidecar recall target ~15ms. "
      "FlashRank adds ON TOP of embedding+search. Go if p95 keeps total well under "
      "the hook timeout; no-go if it dominates.")
