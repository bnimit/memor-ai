"""Re-distillation A/B (the write-side payoff test). Re-distill a slice of
sessions that currently have EXTRACTIVE memories into crisp LLM facts (qwen-14b),
add them to a COPY DB alongside the old extractive ones, then paired temp=0 eval:
  V_extract = recall excluding the new LLM ids (only extractive memories live)
  V_llm     = recall excluding the old extractive ids (only LLM memories live)
Judge only cases where the two recalls differ. Does LLM distillation beat
extractive blobs on win/do-no-harm?
"""
import os, json, re, hashlib, shutil, time
from collections import Counter
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.types import Artifact
from memor.recall import recall
from memor.query_complexity import route_query, Tier
from memor.eval.counterfactual import (
    build_cases_from_store, parse_verdict_json, COUNTERFACTUAL_PROMPT, Outcome)
from memor.llm.openai_compat import OpenAICompatLLM
from memor.llm.base import DISTILL_PROMPT
from memor.tokencount import count_tokens

LIVE = os.path.expanduser("~/.memor/memor.db")
EVAL = "/tmp/memor_redistill_eval.db"
SLICE = 100000  # re-distill ALL extractive-memory sessions (full powered eval)
e = LocalEmbedder()
llm = OpenAICompatLLM("http://192.168.1.6:1234/v1", "lmstudio",
                      "qwen2.5-14b-instruct-mlx", temperature=0)

shutil.copy(LIVE, EVAL)
for ext in ("-wal", "-shm"):
    if os.path.exists(LIVE + ext): shutil.copy(LIVE + ext, EVAL + ext)
s = SqliteStore(EVAL, dim=e.dim)

# sessions with extractive memories
sids = [r["sid"] for r in s.db.execute(
    "SELECT DISTINCT json_extract(meta,'$.session_id') sid FROM artifacts "
    "WHERE kind='memory' AND active=1 AND json_extract(meta,'$.mem_type')='extract' "
    "AND sid IS NOT NULL").fetchall()]
print(f"sessions with extractive memories: {len(sids)}; re-distilling {min(SLICE,len(sids))}\n", flush=True)

def extract_json(raw):
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    return json.loads(m.group(1).strip() if m else raw.strip())

old_ids, new_ids, slice_projects = set(), set(), set()
done = 0
for sid in sids:
    if done >= SLICE: break
    chunks = [s._row_to_artifact(r) for r in s.db.execute(
        "SELECT * FROM artifacts WHERE kind='session_chunk' AND active=1 "
        "AND json_extract(meta,'$.session_id')=?", (sid,)).fetchall()]
    if len(chunks) < 4: continue
    chunks.sort(key=lambda a: a.meta.get("ord", 0))
    project = chunks[0].project
    sel = chunks[:10] + chunks[-5:] if sum(c.token_count for c in chunks) > 4000 else chunks
    txt = "\n".join(c.text for c in sel)[:8000]
    try:
        mems = extract_json(llm.complete(DISTILL_PROMPT.format(session_text=txt), max_tokens=1200)).get("memories", [])
    except Exception:
        continue
    if not mems: continue
    # old extractive memory ids for this session
    for r in s.db.execute("SELECT id FROM artifacts WHERE kind='memory' AND active=1 "
        "AND json_extract(meta,'$.session_id')=? AND json_extract(meta,'$.mem_type')='extract'", (sid,)):
        old_ids.add(r["id"])
    # add new LLM memories
    arts, ts = [], max(c.created_at for c in chunks)
    for m in mems:
        t = (m.get("text") or "").strip()
        if not t: continue
        mid = "llm_" + hashlib.sha1((sid + t).encode()).hexdigest()[:16]
        arts.append(Artifact(id=mid, kind="memory", project=project, source="distill",
                             text=t, token_count=count_tokens(t), created_at=ts,
                             meta={"mem_type": m.get("type", "lesson"), "session_id": sid}))
        new_ids.add(mid)
    if arts:
        s.add_artifacts(arts, e.embed([a.text for a in arts]))
        slice_projects.add(project); done += 1
        print(f"  [{done}] {project:<12} {sid[:12]} extractive->{len(arts)} LLM memories", flush=True)

print(f"\nre-distilled {done} sessions; old_extractive={len(old_ids)} new_llm={len(new_ids)}\n", flush=True)

def judge(case, exclude):
    tier = route_query(case.query)
    if tier == Tier.SKIP: return None, set()
    r = recall(case.query, case.scope_project, EVAL, embedder=e, k=tier.k, threshold=0.15,
               max_tokens=tier.max_tokens, session_id=case.session_id, exclude_ids=exclude)
    return r, set(r.hit_ids or [])

def verdict(case, r):
    if not r.hit_ids: return Outcome.TIE.value
    p = COUNTERFACTUAL_PROMPT.format(query=case.query, holdout="\n".join(case.holdout_texts),
                                     recalled_context=r.formatted_context)
    try: return parse_verdict_json(llm.complete(p)).outcome.value
    except Exception as ex: return f"err:{type(ex).__name__}"

ex_c, llm_c = Counter(), Counter()
improved = worsened = judged = 0
for p in slice_projects:
    for c in build_cases_from_store(s, project=p, holdout_turns=2, min_session_turns=4):
        re_, hits_ex = judge(c, new_ids)   # V_extract: exclude LLM -> only extractive live
        rl_, hits_llm = judge(c, old_ids)   # V_llm: exclude extractive -> only LLM live
        if re_ is None: continue
        if hits_ex == hits_llm: continue  # identical recall -> tie, skip judging
        ov, lv = verdict(c, re_), verdict(c, rl_)
        ex_c[ov] += 1; llm_c[lv] += 1; judged += 1
        if ov == "loss" and lv in ("win","tie"): improved += 1
        elif ov in ("win","tie") and lv == "loss": worsened += 1
        print(f"  [{judged}] {p:<12} extract={ov:<5} llm={lv:<5}", flush=True)

print(f"\n=== re-distillation A/B (cases where recall differed: {judged}) ===")
for nm, c in [("extractive", ex_c), ("llm-distilled", llm_c)]:
    w,t,l = c.get("win",0),c.get("tie",0),c.get("loss",0); j=w+t+l
    print(f"  {nm:<14} win={w} tie={t} loss={l}  do-no-harm={100*(1-l/j) if j else 0:.0f}%")
print(f"\nflips: improved(extract-loss/no-harm -> llm-better)={improved}  worsened={worsened}")
