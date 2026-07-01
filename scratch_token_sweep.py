"""Token-budget sweep (LLM judge). Hold widen+stratify fixed, vary the injected
token cap, and compare do-no-harm + avg tokens to baseline over the same cases.
Goal: keep the counterfactual do-no-harm gain at a token cost <= baseline.
"""
import os
from collections import Counter
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.recall import recall
from memor.query_complexity import route_query, Tier
from memor.eval.counterfactual import (
    build_cases_from_store, parse_verdict_json, COUNTERFACTUAL_PROMPT, Outcome)
from memor.llm.openai_compat import OpenAICompatLLM

DB = os.path.expanduser("~/.memor/memor.db")
PROJECTS = ["plirin", "Memorable", "stablex-saas", "reearth-flow", "ygo", "polymarket"]
CAP_PER_PROJECT = 14

e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)
llm = OpenAICompatLLM("http://192.168.1.6:1234/v1", "lmstudio", "qwen2.5-14b-instruct-mlx")

# (candidate_pool, pool_per_kind, kind_weight, token_cap)  cap=None -> full tier budget
CONFIGS = {
    "baseline (full budget)":  (None, None, 0.15, None),
    "widen+strat @cap500":     (128,  64,   0.15, 500),
    "widen+strat @cap800":     (128,  64,   0.15, 800),
}

cases = []
for p in PROJECTS:
    cases.extend(build_cases_from_store(s, project=p, holdout_turns=2,
                                        min_session_turns=4)[:CAP_PER_PROJECT])
print(f"cases: {len(cases)}\n", flush=True)


def judge(case, cp, ppk, kw, cap):
    tier = route_query(case.query)
    if tier == Tier.SKIP:
        return Outcome.TIE.value, 0
    pool = cp if cp is not None else tier.k
    mt = min(tier.max_tokens, cap) if cap else tier.max_tokens
    r = recall(case.query, case.scope_project, DB, embedder=e, k=tier.k,
               threshold=0.15, max_tokens=mt, session_id=case.session_id,
               candidate_pool=pool, pool_per_kind=ppk, kind_weight=kw)
    if not r.hit_ids:
        return Outcome.TIE.value, 0
    prompt = COUNTERFACTUAL_PROMPT.format(
        query=case.query, holdout="\n".join(case.holdout_texts),
        recalled_context=r.formatted_context)
    try:
        return parse_verdict_json(llm.complete(prompt)).outcome.value, r.tokens_injected
    except Exception as ex:
        return f"error:{type(ex).__name__}", r.tokens_injected


res = {n: {"c": Counter(), "tok": []} for n in CONFIGS}
for i, c in enumerate(cases, 1):
    line = f"[{i}/{len(cases)}]"
    for name, (cp, ppk, kw, cap) in CONFIGS.items():
        o, t = judge(c, cp, ppk, kw, cap)
        res[name]["c"][o] += 1
        if t > 0:
            res[name]["tok"].append(t)
        line += f"  {name.split()[0]}={o[:4]}"
    print(line, flush=True)

print("\n=== token-budget sweep summary ===")
for name in CONFIGS:
    c = res[name]["c"]; tok = res[name]["tok"]
    win, tie, loss = c.get("win", 0), c.get("tie", 0), c.get("loss", 0)
    judged = win + tie + loss
    dnh = (1 - loss / judged) * 100 if judged else 0
    avg = sum(tok) / len(tok) if tok else 0
    print(f"{name:<26} win={win:>2} tie={tie:>2} loss={loss:>2}  "
          f"do-no-harm={dnh:5.1f}%  avg_tokens={avg:4.0f}")
