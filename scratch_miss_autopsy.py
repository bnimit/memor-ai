"""Miss autopsy (no LLM). For each RETRIEVAL_MISS case, trace WHERE the helpful
memory fell out of the production pipeline, to decide the right fix.

helpful memory = top in-project artifact by *pure cosine* to the HOLDOUT (what
the task needed), excluding the same session, sim>=0.4. Cosine oracle on purpose
— independent of the blended Retriever we plan to change, so the gate stays valid.

MISS = helpful exists but production recall() (on the opening query) didn't
surface it. For each miss we rank the helpful memory by cosine to the QUERY
vector, both globally and in-project (exact, via numpy over stored embeddings):

  LOW_SIM_TO_QUERY  cosine(helpful, query) < 0.15   -> query off-topic for it;
                    retrieval can't recover this.
  ABSENT_FROM_KNN   global cosine rank > 200 (knn_fetch) -> global-then-filter
                    KNN never fetched it; PROJECT-SCOPED KNN is the fix.
  KNN_TRUNCATED     in global fetch, in-project rank > 8 (rows[:k]) -> the
                    rows[:k] handoff discarded it; WIDEN/STRATIFY is the fix.
  CANDIDATE_DROPPED in-project rank <= 8 (it WAS a candidate) yet missed ->
                    blend/threshold/quality dropped it; REWEIGHT/threshold fix.
"""
import os, struct
import numpy as np
from collections import Counter
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.types import Scope
from memor.eval.counterfactual import build_cases_from_store
from memor.query_complexity import route_query, Tier
from memor.recall import recall

DB = os.path.expanduser("~/.memor/memor.db")
PROJECTS = ["plirin", "Memorable", "stablex-saas", "reearth-flow", "ygo", "polymarket"]
KNN_FETCH, CURRENT_RETURN, HELPFUL_SIM, LOW_SIM = 200, 8, 0.4, 0.15

e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)

# --- one-time load of all active embeddings into a normalized matrix ---
print("loading embeddings...", flush=True)
rows = s.db.execute("""
  SELECT a.id, a.project, v.embedding
  FROM artifacts a JOIN vec_artifacts v ON a.rowid = v.rowid
  WHERE a.active = 1
""").fetchall()
ids = [r["id"] for r in rows]
projs = np.array([r["project"] for r in rows])
M = np.array([struct.unpack(f"{e.dim}f", r["embedding"]) for r in rows], dtype=np.float32)
M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
id_to_row = {aid: i for i, aid in enumerate(ids)}
print(f"  {len(ids)} active artifacts loaded", flush=True)


def ranks(query_text, target_id, project):
    qv = e.embed([query_text])[0]
    qv = np.asarray(qv, dtype=np.float32); qv /= (np.linalg.norm(qv) + 1e-9)
    sims = M @ qv
    ti = id_to_row[target_id]
    st = sims[ti]
    gr = int((sims > st).sum()) + 1
    mask = (projs == project) | (projs == "_global")
    pr = int((sims[mask] > st).sum()) + 1
    return gr, pr, float(st)


buckets = Counter()
detail = []
n_cases = n_miss = 0
for p in PROJECTS:
    for c in build_cases_from_store(s, project=p, holdout_turns=2, min_session_turns=4):
        n_cases += 1
        qv_hold = e.embed(["\n".join(c.holdout_texts)])[0]
        oracle = [(a.id, sm) for a, sm in s.search(qv_hold, Scope(project=p), 50)
                  if a.meta.get("session_id") != c.session_id and sm >= HELPFUL_SIM]
        if not oracle:
            continue
        helpful = {aid for aid, _ in oracle[:5]}
        best_id = oracle[0][0]
        if best_id not in id_to_row:
            continue
        tier = route_query(c.query)
        prod = set() if tier == Tier.SKIP else set(recall(
            c.query, p, DB, embedder=e, k=tier.k, threshold=0.15,
            max_tokens=tier.max_tokens, session_id=c.session_id).hit_ids or [])
        if helpful & prod:
            continue
        n_miss += 1
        gr, pr, sim = ranks(c.query, best_id, p)
        if sim < LOW_SIM:
            b = "LOW_SIM_TO_QUERY"
        elif gr > KNN_FETCH:
            b = "ABSENT_FROM_KNN"
        elif pr > CURRENT_RETURN:
            b = "KNN_TRUNCATED"
        else:
            b = "CANDIDATE_DROPPED"
        buckets[b] += 1
        detail.append((p, b, gr, pr, round(sim, 3), c.query[:42]))

print(f"\ntotal cases: {n_cases}   misses: {n_miss}\n")
print("=== miss attribution ===")
for b in ["LOW_SIM_TO_QUERY", "ABSENT_FROM_KNN", "KNN_TRUNCATED", "CANDIDATE_DROPPED"]:
    pct = 100 * buckets[b] / n_miss if n_miss else 0
    print(f"  {b:<18} {buckets[b]:>3}  ({pct:.0f}%)")
print("\n=== per-miss (project, bucket, global_rank, inproj_rank, sim, query) ===")
for d in sorted(detail, key=lambda x: x[1]):
    print(f"  {d[0]:<13} {d[1]:<18} g={d[2]:>5} p={d[3]:>4} sim={d[4]:<6} {d[5]!r}")
