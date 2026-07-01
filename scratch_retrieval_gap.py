"""Retrieval-gap diagnostic (no LLM). For each counterfactual case:
  - oracle: is there a prior-session, same-project memory similar to the HOLDOUT
    (what the task actually needed)? -> a helpful memory existed.
  - production: did recall() (querying on the task opening) surface it?
Buckets each case: VALUE_GAP (no helpful memory) / RETRIEVAL_MISS (helpful existed,
not surfaced) / RETRIEVED (helpful existed and surfaced).
"""
import os, sys
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.retrieve.retriever import Retriever
from memor.types import Scope
from memor.eval.counterfactual import build_cases_from_store
from memor.query_complexity import route_query, Tier
from memor.recall import recall

DB = os.path.expanduser("~/.memor/memor.db")
PROJECTS = ["plirin", "Memorable", "stablex-saas", "reearth-flow", "ygo", "polymarket"]
T_RELS = [0.3, 0.4, 0.5]

e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)

def oracle_topk(holdout_text, project, exclude_sid, k=10):
    r = Retriever(s, e, k=k, min_similarity=0.0)
    trace = r.query(holdout_text, Scope(project=project))
    out = []
    for h in trace.hits:
        if h.artifact.meta.get("session_id") == exclude_sid:
            continue
        out.append((h.artifact.id, h.score))
    return out

tot = {t: {"VALUE_GAP":0,"RETRIEVAL_MISS":0,"RETRIEVED":0} for t in T_RELS}
n_cases = 0
for p in PROJECTS:
    cases = build_cases_from_store(s, project=p, holdout_turns=2, min_session_turns=4)
    for c in cases:
        n_cases += 1
        holdout = "\n".join(c.holdout_texts)
        orc = oracle_topk(holdout, p, c.session_id, k=10)
        orc_best = orc[0][1] if orc else 0.0
        orc_ids = {aid for aid,_ in orc[:5]}
        tier = route_query(c.query)
        if tier == Tier.SKIP:
            prod_ids = set()
        else:
            res = recall(c.query, p, DB, embedder=e, k=tier.k, threshold=0.15,
                         max_tokens=tier.max_tokens, session_id=c.session_id)
            prod_ids = set(res.hit_ids or [])
        for t in T_RELS:
            if orc_best < t:
                tot[t]["VALUE_GAP"] += 1
            elif orc_ids & prod_ids:
                tot[t]["RETRIEVED"] += 1
            else:
                tot[t]["RETRIEVAL_MISS"] += 1

print(f"total cases: {n_cases}\n")
for t in T_RELS:
    d = tot[t]; n = n_cases
    print(f"=== relevance threshold (oracle sim to holdout) >= {t} ===")
    print(f"  VALUE_GAP      (no helpful prior memory): {d['VALUE_GAP']:>3}  ({100*d['VALUE_GAP']/n:.0f}%)")
    print(f"  RETRIEVAL_MISS (helpful existed, missed): {d['RETRIEVAL_MISS']:>3}  ({100*d['RETRIEVAL_MISS']/n:.0f}%)")
    print(f"  RETRIEVED      (helpful existed, found):  {d['RETRIEVED']:>3}  ({100*d['RETRIEVED']/n:.0f}%)")
    print()
