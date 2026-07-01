"""Recall latency benchmark (no LLM). Times production recall() over real recent
queries with the widened pool, reports p50/p95, and prints the historical
recall_log average (old-code baseline) for comparison."""
import os, time
import numpy as np
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.recall import recall
from memor.query_complexity import route_query, Tier

DB = os.path.expanduser("~/.memor/memor.db")
e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)

# real recent non-trivial queries from the log, across projects
rows = s.db.execute("""
  SELECT project, query_preview FROM recall_log
  WHERE status IN ('ok','extractive_only','no_hits') AND length(query_preview) > 15
  ORDER BY timestamp DESC LIMIT 200
""").fetchall()

samples = []
for r in rows:
    if route_query(r["query_preview"]) == Tier.SKIP:
        continue
    samples.append((r["project"], r["query_preview"]))
samples = samples[:120]

# warm the embedder/model
recall("warmup query", "Memorable", DB, embedder=e, k=8, threshold=0.15)

lat = []
for proj, q in samples:
    t0 = time.perf_counter()
    recall(q, proj, DB, embedder=e, k=8, threshold=0.15, max_tokens=1500)
    lat.append((time.perf_counter() - t0) * 1000)

lat = np.array(lat)
print(f"recall() latency over {len(lat)} real queries (new widened pool):")
print(f"  p50 = {np.percentile(lat,50):.1f} ms")
print(f"  p95 = {np.percentile(lat,95):.1f} ms")
print(f"  max = {lat.max():.1f} ms")

hist = s.get_recall_stats()
print(f"\nhistorical recall_log avg latency (old code, all-time): "
      f"{hist['avg_latency_ms']} ms  over {hist['total_recalls']} recalls")
