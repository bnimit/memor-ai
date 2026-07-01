"""Eval-trust test: is the local qwen judge repeatable at temperature=0?
Run the SAME baseline config twice over the same cases with a temp=0 judge and
measure case-by-case agreement. If agreement ~100%, the eval is trustworthy and
the earlier 82%<->88% swing was sampling temperature. If it still diverges, the
14B judge can't resolve small effects.
"""
import os
import httpx
from collections import Counter
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.recall import recall
from memor.query_complexity import route_query, Tier
from memor.eval.counterfactual import (
    build_cases_from_store, parse_verdict_json, COUNTERFACTUAL_PROMPT, Outcome)

DB = os.path.expanduser("~/.memor/memor.db")
PROJECTS = ["plirin", "Memorable", "stablex-saas", "reearth-flow", "ygo", "polymarket"]
CAP_PER_PROJECT = 8
URL = "http://192.168.1.6:1234/v1/chat/completions"
MODEL = "qwen2.5-14b-instruct-mlx"

e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)


def judge_temp0(prompt):
    r = httpx.post(URL, headers={"Authorization": "Bearer lmstudio"},
                   json={"model": MODEL, "temperature": 0, "max_tokens": 1024,
                         "messages": [{"role": "user", "content": prompt}]},
                   timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


cases = []
for p in PROJECTS:
    cases.extend(build_cases_from_store(s, project=p, holdout_turns=2,
                                        min_session_turns=4)[:CAP_PER_PROJECT])
print(f"cases: {len(cases)}\n", flush=True)


def outcome_for(case):
    tier = route_query(case.query)
    if tier == Tier.SKIP:
        return Outcome.TIE.value
    r = recall(case.query, case.scope_project, DB, embedder=e, k=tier.k,
               threshold=0.15, max_tokens=tier.max_tokens, session_id=case.session_id)
    if not r.hit_ids:
        return Outcome.TIE.value
    prompt = COUNTERFACTUAL_PROMPT.format(
        query=case.query, holdout="\n".join(case.holdout_texts),
        recalled_context=r.formatted_context)
    try:
        return parse_verdict_json(judge_temp0(prompt)).outcome.value
    except Exception as ex:
        return f"error:{type(ex).__name__}"


runA, runB = [], []
agree = 0
for i, c in enumerate(cases, 1):
    a = outcome_for(c)
    b = outcome_for(c)
    runA.append(a); runB.append(b)
    if a == b:
        agree += 1
    flag = "" if a == b else "  <-- DIVERGED"
    print(f"[{i}/{len(cases)}] runA={a:<5} runB={b:<5}{flag}", flush=True)

n = len(cases)
print(f"\n=== determinism @ temp=0 ===")
print(f"case-by-case agreement: {agree}/{n} ({100*agree/n:.0f}%)")
for label, run in [("runA", runA), ("runB", runB)]:
    c = Counter(run)
    print(f"  {label}: win={c.get('win',0)} tie={c.get('tie',0)} loss={c.get('loss',0)}")
