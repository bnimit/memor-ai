"""How much of the 23% full-source rescue ceiling does a REALISTIC no-LLM
keyphrase enrichment recover? For each memory-miss, enrich the memory with
frequency-based keyphrases of its source session (no LLM) and compare rescue %
to the full-source upper bound.
"""
import os, struct, re, json
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
HELPFUL_SIM, CANDIDATE_RANGE, SRC_CAP = 0.4, 50, 4000
STOP = set("the a an and or of to in for on with is are was were be been being it as at by from "
           "this that these those i you we they he she but if then so not no yes do does did has have "
           "had will would can could should may might our your their its his her them us me my will "
           "what which who when where why how can your you are use used using also into out up down "
           "over under more most some any all each via per".split())

e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)
rows = s.db.execute("""SELECT a.id,a.project,a.kind,a.text,a.meta,v.embedding
  FROM artifacts a JOIN vec_artifacts v ON a.rowid=v.rowid WHERE a.active=1""").fetchall()
ids=[r["id"] for r in rows]; proj=np.array([r["project"] for r in rows]); kind=np.array([r["kind"] for r in rows])
text={r["id"]:r["text"] for r in rows}
sess={r["id"]:(json.loads(r["meta"]).get("session_id") if r["meta"] else None) for r in rows}
M=np.array([struct.unpack(f"{e.dim}f",r["embedding"]) for r in rows],dtype=np.float32); M/=(np.linalg.norm(M,axis=1,keepdims=True)+1e-9)
id_to_row={a:i for i,a in enumerate(ids)}
chunks_by_sess={}
for r in rows:
    if r["kind"]=="session_chunk" and sess[r["id"]]:
        chunks_by_sess.setdefault(sess[r["id"]],[]).append(r["text"] or "")


def keyphrases(texts, topn):
    words=[w for w in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}"," ".join(texts).lower()) if w not in STOP]
    if not words: return ""
    uni=[w for w,_ in Counter(words).most_common(topn)]
    big=[f"{a} {b}" for (a,b),_ in Counter(zip(words,words[1:])).most_common(topn//2)]
    return " ".join(uni+big)


def rank_of(qv, project, sim):
    mask=(proj==project); sims=M[mask]@qv
    return int((sims>sim).sum())+1


VARIANTS=["full_source","keyphrase20","keyphrase40"]
res={v:Counter() for v in VARIANTS}
n=0
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
        src_chunks=chunks_by_sess.get(sess.get(Mh.id) or "",[])
        enrich_texts={
            "full_source":(text[Mh.id]+" "+" ".join(src_chunks))[:SRC_CAP],
            "keyphrase20":text[Mh.id]+" "+keyphrases(src_chunks,20),
            "keyphrase40":text[Mh.id]+" "+keyphrases(src_chunks,40),
        }
        for v in VARIANTS:
            ev=np.asarray(e.embed([enrich_texts[v]])[0],dtype=np.float32); ev/=(np.linalg.norm(ev)+1e-9)
            r_enr=rank_of(qv,p,float(ev@qv))
            if rank_cur>CANDIDATE_RANGE and r_enr<=CANDIDATE_RANGE: res[v]["RESCUED"]+=1
            elif r_enr<rank_cur: res[v]["improved"]+=1
            else: res[v]["no_help"]+=1

print(f"\nmemory-misses: {n}\n")
print(f"{'variant':<14} {'RESCUED':>9} {'improved':>9} {'no_help':>9}")
for v in VARIANTS:
    c=res[v]
    print(f"{v:<14} {c['RESCUED']:>4} ({100*c['RESCUED']/n:>2.0f}%) {c['improved']:>9} {c['no_help']:>9}")
