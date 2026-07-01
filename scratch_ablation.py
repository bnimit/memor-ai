"""Ablation for distilled-aware retrieval (no LLM). Frozen pure-cosine oracle;
for each config, measure RETRIEVAL_MISS and how often a distilled memory was
surfaced. Isolates: widen vs +stratify vs +reweight.

baseline = old behavior (candidate_pool == k, no stratify, kind_weight 0.15).
"""
import os
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.types import Scope
from memor.eval.counterfactual import build_cases_from_store
from memor.query_complexity import route_query, Tier
from memor.recall import recall

DB = os.path.expanduser("~/.memor/memor.db")
PROJECTS = ["plirin", "Memorable", "stablex-saas", "reearth-flow", "ygo", "polymarket"]
HELPFUL_SIM = 0.4

e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)
KIND = {r["id"]: r["kind"] for r in s.db.execute(
    "SELECT id, kind FROM artifacts WHERE active=1").fetchall()}

# config -> (candidate_pool_or_None_means_k, pool_per_kind, kind_weight)
CONFIGS = {
    "baseline (k, no-strat, kw.15)": (None, None, 0.15),
    "widen128 kw.15":               (128,  None, 0.15),
    "widen128+strat64 kw.15":       (128,  64,   0.15),
    "widen128+strat64 kw.20":       (128,  64,   0.20),
    "widen128+strat64 kw.25":       (128,  64,   0.25),
}

# precompute cases + frozen oracle once
cases = []
for p in PROJECTS:
    for c in build_cases_from_store(s, project=p, holdout_turns=2, min_session_turns=4):
        qv_hold = e.embed(["\n".join(c.holdout_texts)])[0]
        oracle = {a.id for a, sm in s.search(qv_hold, Scope(project=p), 50)
                  if a.meta.get("session_id") != c.session_id and sm >= HELPFUL_SIM}
        if oracle:
            cases.append((p, c, oracle))
print(f"cases with a helpful memory: {len(cases)}\n")

results = {}
for name, (cp, ppk, kw) in CONFIGS.items():
    miss = 0
    distilled_surfaced = 0  # cases where >=1 distilled memory was injected
    for p, c, oracle in cases:
        tier = route_query(c.query)
        if tier == Tier.SKIP:
            ids = []
        else:
            pool = cp if cp is not None else tier.k
            r = recall(c.query, p, DB, embedder=e, k=tier.k, threshold=0.15,
                       max_tokens=tier.max_tokens, session_id=c.session_id,
                       candidate_pool=pool, pool_per_kind=ppk, kind_weight=kw)
            ids = r.hit_ids or []
        if not (oracle & set(ids)):
            miss += 1
        if any(KIND.get(i) == "memory" for i in ids):
            distilled_surfaced += 1
    results[name] = (miss, distilled_surfaced)

n = len(cases)
print(f"{'config':<32} {'misses':>7} {'miss%':>6} {'distilled-surfaced':>18}")
for name, (miss, ds) in results.items():
    print(f"{name:<32} {miss:>7} {100*miss/n:>5.0f}% {ds:>13} ({100*ds/n:.0f}%)")
