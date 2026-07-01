"""H1 candidacy-rescue test: does REAL LLM enrichment (A-MEM-style keywords/tags +
self-contained rewrite, qwen-14b) pull memory-misses into candidate range?
Compare to the no-LLM keyphrase floor (6-9%) and full-source ceiling (23%).

For each memory-miss (helpful distilled memory that production didn't surface):
  enrich it with the local LLM, embed concat(rewrite, keywords, tags), and
  re-rank by cosine to the QUERY among in-project artifacts.
  rescued = current rank > 50 AND enriched rank <= 50.
"""
import os, struct, json, re
import numpy as np
from collections import Counter
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.types import Scope
from memor.eval.counterfactual import build_cases_from_store
from memor.query_complexity import route_query, Tier
from memor.recall import recall
from memor.llm.openai_compat import OpenAICompatLLM

DB = os.path.expanduser("~/.memor/memor.db")
PROJECTS = ["plirin", "Memorable", "stablex-saas", "reearth-flow", "ygo", "polymarket"]
HELPFUL_SIM, CANDIDATE_RANGE, SRC_CAP = 0.4, 50, 3000
e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)
llm = OpenAICompatLLM("http://192.168.1.6:1234/v1", "lmstudio",
                      "qwen2.5-14b-instruct-mlx", temperature=0)

rows = s.db.execute("""SELECT a.id,a.project,a.kind,a.text,a.meta,v.embedding
  FROM artifacts a JOIN vec_artifacts v ON a.rowid=v.rowid WHERE a.active=1""").fetchall()
ids=[r["id"] for r in rows]; proj=np.array([r["project"] for r in rows])
text={r["id"]:r["text"] for r in rows}
sess={r["id"]:(json.loads(r["meta"]).get("session_id") if r["meta"] else None) for r in rows}
M=np.array([struct.unpack(f"{e.dim}f",r["embedding"]) for r in rows],dtype=np.float32); M/=(np.linalg.norm(M,axis=1,keepdims=True)+1e-9)
id_to_row={a:i for i,a in enumerate(ids)}
chunks_by_sess={}
for r in rows:
    if r["kind"]=="session_chunk" and sess[r["id"]]:
        chunks_by_sess.setdefault(sess[r["id"]],[]).append(r["text"] or "")

ENRICH = """Given this distilled coding memory and an excerpt of its source session,
output STRICT JSON: {{"rewrite":"<self-contained version of the memory; resolve pronouns/implicit refs so it stands alone>","keywords":["exact identifiers, file/API/tool names, error strings a future query might use"],"tags":["2-5 short topic tags"]}}
MEMORY: {mem}
SOURCE EXCERPT: {src}"""

def enrich(mem_text, src):
    try:
        raw = llm.complete(ENRICH.format(mem=mem_text, src=src[:SRC_CAP]), max_tokens=400)
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        d = json.loads(m.group(1).strip() if m else raw.strip())
        return " ".join([d.get("rewrite",mem_text)," ".join(d.get("keywords",[]))," ".join(d.get("tags",[]))])
    except Exception:
        return None

def rank_of(qv, project, sim):
    mask=(proj==project); return int((M[mask]@qv > sim).sum())+1

buckets=Counter(); n=0; err=0
for p in PROJECTS:
    for c in build_cases_from_store(s, project=p, holdout_turns=2, min_session_turns=4):
        tier=route_query(c.query)
        if tier==Tier.SKIP: continue
        qv_h=e.embed(["\n".join(c.holdout_texts)])[0]
        om=[a for a,sm in s.search(qv_h,Scope(project=p),50)
            if a.kind=="memory" and a.meta.get("session_id")!=c.session_id and sm>=HELPFUL_SIM]
        if not om: continue
        Mh=om[0]
        prod=set(recall(c.query,p,DB,embedder=e,k=tier.k,threshold=0.15,
                        max_tokens=tier.max_tokens,session_id=c.session_id).hit_ids or [])
        if Mh.id in prod: continue
        n+=1
        qv=np.asarray(e.embed([c.query])[0],dtype=np.float32); qv/=(np.linalg.norm(qv)+1e-9)
        rank_cur=rank_of(qv,p,float(M[id_to_row[Mh.id]]@qv))
        et=enrich(text[Mh.id], " ".join(chunks_by_sess.get(sess.get(Mh.id) or "",[])))
        if et is None: err+=1; continue
        ev=np.asarray(e.embed([et])[0],dtype=np.float32); ev/=(np.linalg.norm(ev)+1e-9)
        r_enr=rank_of(qv,p,float(ev@qv))
        if rank_cur>CANDIDATE_RANGE and r_enr<=CANDIDATE_RANGE: buckets["RESCUED"]+=1
        elif r_enr<rank_cur: buckets["improved"]+=1
        else: buckets["no_help"]+=1
        print(f"[{n}] {p:<12} rank {rank_cur:>4} -> {r_enr:<4}"
              f"{'  RESCUED' if (rank_cur>CANDIDATE_RANGE and r_enr<=CANDIDATE_RANGE) else ''}",flush=True)

print(f"\n=== H1 LLM-enriched candidacy (n={n}, errors={err}) ===")
for b in ["RESCUED","improved","no_help"]:
    print(f"  {b:<10} {buckets[b]:>3} ({100*buckets[b]/n if n else 0:.0f}%)")
print("\nreference: no-LLM keyphrase floor ~6-9%, full-source ceiling ~23%")
