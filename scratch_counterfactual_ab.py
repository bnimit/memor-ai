"""A/B counterfactual eval (LLM judge). The real do-no-harm / win-rate decider.
Compares BASELINE (no widen) vs WIDEN+STRATIFY over the same cases, judged by the
local qwen-14b. Reports win/tie/loss, do-no-harm, and avg tokens injected.
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
CAP_PER_PROJECT = 14  # bound LLM calls

e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)
llm = OpenAICompatLLM("http://192.168.1.6:1234/v1", "lmstudio", "qwen2.5-14b-instruct-mlx")

CONFIGS = {
    "baseline":         dict(candidate_pool=None, pool_per_kind=None, kind_weight=0.15),
    "widen+stratify":   dict(candidate_pool=128,  pool_per_kind=64,   kind_weight=0.15),
}

cases = []
for p in PROJECTS:
    cs = build_cases_from_store(s, project=p, holdout_turns=2, min_session_turns=4)
    cases.extend(cs[:CAP_PER_PROJECT])
print(f"cases: {len(cases)}\n", flush=True)


def judge(case, cfg):
    tier = route_query(case.query)
    if tier == Tier.SKIP:
        return Outcome.TIE.value, 0
    cp = cfg["candidate_pool"] if cfg["candidate_pool"] is not None else tier.k
    r = recall(case.query, case.scope_project, DB, embedder=e, k=tier.k,
               threshold=0.15, max_tokens=tier.max_tokens, session_id=case.session_id,
               candidate_pool=cp, pool_per_kind=cfg["pool_per_kind"],
               kind_weight=cfg["kind_weight"])
    if not r.hit_ids:
        return Outcome.TIE.value, 0
    prompt = COUNTERFACTUAL_PROMPT.format(
        query=case.query, holdout="\n".join(case.holdout_texts),
        recalled_context=r.formatted_context)
    try:
        v = parse_verdict_json(llm.complete(prompt))
        return v.outcome.value, r.tokens_injected
    except Exception as ex:
        return f"error:{type(ex).__name__}", r.tokens_injected


results = {name: {"counts": Counter(), "tokens": [], "n_inject": 0} for name in CONFIGS}
for i, c in enumerate(cases, 1):
    line = f"[{i}/{len(cases)}] {c.scope_project[:10]:<10}"
    for name, cfg in CONFIGS.items():
        outcome, toks = judge(c, cfg)
        results[name]["counts"][outcome] += 1
        if toks > 0:
            results[name]["tokens"].append(toks)
            results[name]["n_inject"] += 1
        line += f"  {name}={outcome}"
    print(line, flush=True)

print("\n=== A/B counterfactual summary ===")
n = len(cases)
for name in CONFIGS:
    c = results[name]["counts"]
    toks = results[name]["tokens"]
    win, tie, loss = c.get("win", 0), c.get("tie", 0), c.get("loss", 0)
    errs = sum(v for k, v in c.items() if k.startswith("error"))
    judged = win + tie + loss
    avg_tok = (sum(toks) / len(toks)) if toks else 0
    dnh = (1 - loss / judged) * 100 if judged else 0
    print(f"\n{name}:")
    print(f"  win={win}  tie={tie}  loss={loss}  errors={errs}  (judged={judged})")
    print(f"  do-no-harm={dnh:.1f}%   win-rate={100*win/judged if judged else 0:.1f}%")
    print(f"  avg tokens injected={avg_tok:.0f}  over {results[name]['n_inject']} injecting cases")
