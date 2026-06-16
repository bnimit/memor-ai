"""Reaffirmation-recency premise + effect-size check (no LLM).

Simulates what `last_reaffirmed` WOULD be on the current corpus, then asks:
  1. Coverage/discriminativeness: what fraction of active memories get reaffirmed
     (at sim thresholds 0.6/0.7/0.8), and how many are the population the change
     actually helps = OLD created_at but RECENTLY reaffirmed ("rescued").
  2. Effect size: over eval cases, in what fraction does a candidate memory
     (a memory in today's top-k by cosine) belong to that rescued population —
     i.e. cases where effective_ts reranking could plausibly change injection.
If rescued population ~0 or eval-impact ~0%, the change is inert -> stop.
"""
import os, re, time
import numpy as np
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.types import Scope
from memor.eval.counterfactual import build_cases_from_store
from memor.query_complexity import route_query, Tier
from memor.distill.distiller import _REPLACEMENT_RE as SUPERSEDE_REGEX

DB = os.path.expanduser("~/.memor/memor.db")
PROJECTS = ["plirin", "Memorable", "stablex-saas", "reearth-flow", "ygo", "polymarket"]
NOW = time.time()
DAY = 86400.0
OLD_DAYS = 14      # "old" = created > 14d ago (past the recency half-life)
FRESH_DAYS = 14    # "recently reaffirmed" = within 14d
THRESHOLDS = [0.6, 0.7, 0.8]

e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)

rows = s.db.execute("""
  SELECT a.id, a.project, a.kind, a.created_at, a.text, v.embedding
  FROM artifacts a JOIN vec_artifacts v ON a.rowid = v.rowid
  WHERE a.active = 1
""").fetchall()
ids = [r["id"] for r in rows]
proj = np.array([r["project"] for r in rows])
kind = np.array([r["kind"] for r in rows])
created = np.array([r["created_at"] for r in rows], dtype=np.float64)
import struct
M = np.array([struct.unpack(f"{e.dim}f", r["embedding"]) for r in rows], dtype=np.float32)
M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
cue = np.array([bool(SUPERSEDE_REGEX.search(r["text"] or "")) for r in rows])
id_to_row = {a: i for i, a in enumerate(ids)}
print(f"loaded {len(ids)} active artifacts "
      f"({int((kind=='memory').sum())} memories, {int((kind=='session_chunk').sum())} chunks)\n",
      flush=True)


def simulate_reaffirm(thresh):
    """Return last_reaffirmed[row] for memory rows (0.0 if never)."""
    reaff = np.zeros(len(ids))
    for p in set(proj):
        pm = np.where((proj == p) & (kind == "memory"))[0]
        pc = np.where((proj == p) & (kind == "session_chunk") & (~cue))[0]  # cue chunks excluded
        if len(pm) == 0 or len(pc) == 0:
            continue
        sims = M[pm] @ M[pc].T                       # (n_mem, n_chunk)
        for i, mi in enumerate(pm):
            later = pc[(created[pc] > created[mi]) & (sims[i] >= thresh)]
            if len(later):
                reaff[mi] = created[later].max()
    return reaff


for t in THRESHOLDS:
    reaff = simulate_reaffirm(t)
    mem = np.where(kind == "memory")[0]
    n_mem = len(mem)
    any_reaff = (reaff[mem] > 0).sum()
    old = created[mem] < (NOW - OLD_DAYS * DAY)
    rescued = (reaff[mem] > NOW - FRESH_DAYS * DAY) & old   # old created, fresh reaffirm
    print(f"thresh={t}:  reaffirmed {any_reaff}/{n_mem} ({100*any_reaff/n_mem:.0f}%)   "
          f"'rescued' (old+fresh-reaffirm) {int(rescued.sum())} ({100*rescued.sum()/n_mem:.0f}%)",
          flush=True)

# effect size at spec threshold 0.6
print("\n=== effect size over eval cases (thresh=0.6) ===", flush=True)
reaff = simulate_reaffirm(0.6)
rescued_ids = {ids[i] for i in np.where(
    (kind == "memory") & (reaff > NOW - FRESH_DAYS * DAY) & (created < NOW - OLD_DAYS * DAY))[0]}
n_cases = impacted = 0
for p in PROJECTS:
    for c in build_cases_from_store(s, project=p, holdout_turns=2, min_session_turns=4):
        tier = route_query(c.query)
        if tier == Tier.SKIP:
            continue
        n_cases += 1
        qv = e.embed([c.query])[0]
        cand = s.search(qv, Scope(project=p), tier.k)
        cand_mem = [a.id for a, _ in cand if a.kind == "memory"]
        if any(mid in rescued_ids for mid in cand_mem):
            impacted += 1
print(f"cases: {n_cases}   cases with a 'rescued' memory in today's candidate set: "
      f"{impacted} ({100*impacted/n_cases:.0f}%)")
print("\n(reminder: max(created_at,last_reaffirmed) only BOOSTS reaffirmed memories; "
      "it never sinks un-reaffirmed ones. Effect on do-harm is indirect re-ranking.)")
