"""Enrichment premise check (no LLM). For eval-case misses where the helpful item
is a distilled MEMORY that wasn't surfaced, test whether enriching the memory's
representation pulls it into candidate range.

Upper-bound enrichment = memory text + its full source-session text (the richest
cheap representation). If even this doesn't improve the memory's cosine rank to
the query, enrichment is futile (the miss is query-mismatch-fundamental). If it
does, the keyphrase/tag variants are worth building.

  rank = position among in-project active artifacts by cosine to the QUERY.
  CANDIDATE_RANGE = a memory needs ~<=50 to realistically enter the pool.
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
HELPFUL_SIM = 0.4
CANDIDATE_RANGE = 50
SRC_CAP = 4000  # chars of source-session text to append

e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)

rows = s.db.execute("""SELECT a.id,a.project,a.kind,a.text,a.meta,v.embedding
  FROM artifacts a JOIN vec_artifacts v ON a.rowid=v.rowid WHERE a.active=1""").fetchall()
import json
ids = [r["id"] for r in rows]
proj = np.array([r["project"] for r in rows]); kind = np.array([r["kind"] for r in rows])
text = {r["id"]: r["text"] for r in rows}
sess = {r["id"]: (json.loads(r["meta"]).get("session_id") if r["meta"] else None) for r in rows}
M = np.array([struct.unpack(f"{e.dim}f", r["embedding"]) for r in rows], dtype=np.float32)
M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
id_to_row = {a: i for i, a in enumerate(ids)}

# source chunks per session (for enrichment text)
chunks_by_sess: dict[str, list[str]] = {}
for r in rows:
    if r["kind"] == "session_chunk" and sess[r["id"]]:
        chunks_by_sess.setdefault(sess[r["id"]], []).append(r["text"] or "")


def inproject_rank(qv_norm, project, target_sim):
    mask = (proj == project)
    sims = M[mask] @ qv_norm
    return int((sims > target_sim).sum()) + 1


buckets = Counter()
detail = []
n_mem_miss = 0
for p in PROJECTS:
    for c in build_cases_from_store(s, project=p, holdout_turns=2, min_session_turns=4):
        tier = route_query(c.query)
        if tier == Tier.SKIP:
            continue
        # oracle helpful MEMORY (enrichment only touches memories)
        qv_h = e.embed(["\n".join(c.holdout_texts)])[0]
        oracle_mem = [a for a, sm in s.search(qv_h, Scope(project=p), 50)
                      if a.kind == "memory" and a.meta.get("session_id") != c.session_id
                      and sm >= HELPFUL_SIM]
        if not oracle_mem:
            continue
        M_help = oracle_mem[0]
        prod = set(recall(c.query, p, DB, embedder=e, k=tier.k, threshold=0.15,
                          max_tokens=tier.max_tokens, session_id=c.session_id).hit_ids or [])
        if M_help.id in prod:
            continue
        n_mem_miss += 1
        qv = np.asarray(e.embed([c.query])[0], dtype=np.float32); qv /= (np.linalg.norm(qv)+1e-9)
        # current rank
        cur_sim = float(M[id_to_row[M_help.id]] @ qv)
        rank_cur = inproject_rank(qv, p, cur_sim)
        # enriched rank (memory text + source-session text)
        src = " ".join(chunks_by_sess.get(sess.get(M_help.id) or "", []))[:SRC_CAP]
        env = e.embed([(text[M_help.id] + " " + src)[:SRC_CAP]])[0]
        env = np.asarray(env, dtype=np.float32); env /= (np.linalg.norm(env)+1e-9)
        enr_sim = float(env @ qv)
        rank_enr = inproject_rank(qv, p, enr_sim)
        rescued = rank_cur > CANDIDATE_RANGE and rank_enr <= CANDIDATE_RANGE
        if rescued:
            buckets["RESCUED"] += 1
        elif rank_enr < rank_cur:
            buckets["improved_not_enough"] += 1
        else:
            buckets["no_help"] += 1
        detail.append((p, rank_cur, rank_enr, "RESCUED" if rescued else ""))

print(f"\nmemory-misses examined: {n_mem_miss}")
print("=== enrichment (upper bound: memory + full source session) ===")
for b in ["RESCUED", "improved_not_enough", "no_help"]:
    pct = 100*buckets[b]/n_mem_miss if n_mem_miss else 0
    print(f"  {b:<20} {buckets[b]:>3} ({pct:.0f}%)")
print("\n=== rank moves (current -> enriched), sample ===")
for d in sorted(detail, key=lambda x: x[1], reverse=True)[:20]:
    print(f"  {d[0]:<13} rank {d[1]:>4} -> {d[2]:<4} {d[3]}")
