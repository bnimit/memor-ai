"""H4 premise (no LLM): how many same-project memory pairs are CONTRADICTION
CANDIDATES — similar topic (cosine in [0.55, 0.92]) but not near-duplicates
(>=0.85 already handled by daemon compaction), one newer than the other?
If ~0, consolidation has nothing to act on. If substantial, proceed to
LLM-judge the pairs + paired eval.
"""
import os, struct, json
import numpy as np
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder

DB = os.path.expanduser("~/.memor/memor.db")
LO, HI = 0.55, 0.85   # contradiction band: similar topic, below the dedup cutoff
e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)

rows = s.db.execute("""SELECT a.id,a.project,a.created_at,a.text,v.embedding
  FROM artifacts a JOIN vec_artifacts v ON a.rowid=v.rowid
  WHERE a.active=1 AND a.kind='memory'""").fetchall()
proj = np.array([r["project"] for r in rows])
created = np.array([r["created_at"] for r in rows], dtype=np.float64)
text = [r["text"] for r in rows]
M = np.array([struct.unpack(f"{e.dim}f", r["embedding"]) for r in rows], dtype=np.float32)
M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
print(f"active memories: {len(rows)}\n")

total_pairs = 0
mems_with_candidate = set()
samples = []
for p in sorted(set(proj)):
    idx = np.where(proj == p)[0]
    if len(idx) < 2:
        continue
    sub = M[idx]
    sims = sub @ sub.T
    for i in range(len(idx)):
        for j in range(i + 1, len(idx)):
            sim = sims[i, j]
            if LO <= sim < HI:
                total_pairs += 1
                mems_with_candidate.add(idx[i]); mems_with_candidate.add(idx[j])
                if len(samples) < 12:
                    a, b = idx[i], idx[j]
                    samples.append((p, round(float(sim), 2), text[a][:70], text[b][:70]))

print(f"contradiction-candidate pairs (cosine {LO}-{HI}): {total_pairs}")
print(f"memories involved: {len(mems_with_candidate)} / {len(rows)} "
      f"({100*len(mems_with_candidate)/len(rows):.0f}%)")
print("\n=== sample candidate pairs (LLM would judge contradict/extend/unrelated) ===")
for pr, sim, a, b in samples:
    print(f"  [{pr} sim={sim}]\n    A: {a}\n    B: {b}")
