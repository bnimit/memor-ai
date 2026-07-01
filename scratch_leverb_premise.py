"""Lever-B premise check (no LLM): do WINS correlate with distilled memories
being recalled, vs raw session_chunks? And how much of the corpus is distilled?
Joins stored faithful (qwen) outcomes per session_id with a fresh production
recall() to see what KIND of artifact was surfaced for each outcome.
"""
import os, json, sqlite3
from collections import defaultdict
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.eval.counterfactual import build_cases_from_store
from memor.query_complexity import route_query, Tier
from memor.recall import recall

DB = os.path.expanduser("~/.memor/memor.db")
PROJECTS = ["plirin", "Memorable", "stablex-saas", "reearth-flow", "ygo"]

e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)
raw = sqlite3.connect(DB); raw.row_factory = sqlite3.Row

# corpus composition
print("=== corpus composition (active artifacts) ===")
for p in PROJECTS:
    rows = raw.execute(
        "SELECT kind, COUNT(*) c FROM artifacts WHERE project=? AND active=1 GROUP BY kind",
        (p,)).fetchall()
    comp = {r["kind"]: r["c"] for r in rows}
    print(f"  {p:<14} distilled(memory)={comp.get('memory',0):>4}  session_chunk={comp.get('session_chunk',0):>5}")

# latest faithful (qwen) outcome per session, per project
def outcomes_for(project):
    r = raw.execute(
        "SELECT id, metrics FROM eval_runs WHERE config LIKE ? ORDER BY id DESC LIMIT 1",
        (f'%\"{project}\"%',)).fetchone()
    m = json.loads(r["metrics"])
    return {c["session_id"]: c["outcome"] for c in m.get("cases", [])}

def kind_of(aid):
    row = raw.execute("SELECT kind FROM artifacts WHERE id=?", (aid,)).fetchone()
    return row["kind"] if row else "?"

bucket = defaultdict(lambda: {"n": 0, "with_distilled": 0, "distilled_hits": 0, "raw_hits": 0})
for p in PROJECTS:
    out = outcomes_for(p)
    for c in build_cases_from_store(s, project=p, holdout_turns=2, min_session_turns=4):
        oc = out.get(c.session_id)
        if oc is None:
            continue
        tier = route_query(c.query)
        hit_ids = [] if tier == Tier.SKIP else (recall(
            c.query, p, DB, embedder=e, k=tier.k, threshold=0.15,
            max_tokens=tier.max_tokens, session_id=c.session_id).hit_ids or [])
        kinds = [kind_of(h) for h in hit_ids]
        nd = sum(1 for k in kinds if k == "memory")
        nr = sum(1 for k in kinds if k == "session_chunk")
        b = bucket[oc]
        b["n"] += 1
        b["with_distilled"] += 1 if nd > 0 else 0
        b["distilled_hits"] += nd
        b["raw_hits"] += nr

print("\n=== recalled artifact kind by outcome (faithful qwen) ===")
print(f'{"outcome":<8} {"n":>3} {"%cases w/ distilled":>20} {"avg distilled/case":>20} {"avg raw/case":>14}')
for oc in ["win", "tie", "loss"]:
    b = bucket[oc]
    if not b["n"]:
        continue
    print(f'{oc:<8} {b["n"]:>3} {100*b["with_distilled"]/b["n"]:>19.0f}% '
          f'{b["distilled_hits"]/b["n"]:>20.2f} {b["raw_hits"]/b["n"]:>14.2f}')
