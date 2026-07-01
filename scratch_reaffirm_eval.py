"""Powered eval for reaffirmation-recency: paired baseline-vs-after counterfactual
at temperature=0, with McNemar on the loss flips. Operates on a COPY of the live
DB (backfills last_reaffirmed there), never the user's real store.
"""
import os, re, time, shutil, struct, math
from contextlib import contextmanager
from unittest.mock import patch
from collections import Counter
import numpy as np
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.recall import recall
from memor.query_complexity import route_query, Tier
from memor.eval.counterfactual import (
    build_cases_from_store, parse_verdict_json, COUNTERFACTUAL_PROMPT, Outcome)
from memor.llm.openai_compat import OpenAICompatLLM
from memor.distill.distiller import _REPLACEMENT_RE

LIVE = os.path.expanduser("~/.memor/memor.db")
EVAL = "/tmp/memor_reaffirm_eval.db"
PROJECTS = ["plirin", "Memorable", "stablex-saas", "reearth-flow", "ygo", "polymarket"]
THRESH = 0.80
e = LocalEmbedder()
llm = OpenAICompatLLM("http://192.168.1.6:1234/v1", "lmstudio",
                      "qwen2.5-14b-instruct-mlx", temperature=0)

print("copying live DB...", flush=True)
shutil.copy(LIVE, EVAL)
for ext in ("-wal", "-shm"):
    if os.path.exists(LIVE + ext):
        shutil.copy(LIVE + ext, EVAL + ext)
s = SqliteStore(EVAL, dim=e.dim)  # runs the migration -> adds last_reaffirmed

# --- backfill last_reaffirmed via numpy (mirrors the production ingest pass) ---
print("backfilling last_reaffirmed @0.80 ...", flush=True)
rows = s.db.execute("""SELECT a.id,a.project,a.kind,a.created_at,a.text,v.embedding
  FROM artifacts a JOIN vec_artifacts v ON a.rowid=v.rowid WHERE a.active=1""").fetchall()
ids = [r["id"] for r in rows]
proj = np.array([r["project"] for r in rows]); kind = np.array([r["kind"] for r in rows])
created = np.array([r["created_at"] for r in rows], dtype=np.float64)
M = np.array([struct.unpack(f"{e.dim}f", r["embedding"]) for r in rows], dtype=np.float32)
M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
cue = np.array([bool(_REPLACEMENT_RE.search(r["text"] or "")) for r in rows])
for p in set(proj):
    pm = np.where((proj == p) & (kind == "memory"))[0]
    pc = np.where((proj == p) & (kind == "session_chunk") & (~cue))[0]
    if not len(pm) or not len(pc):
        continue
    sims = M[pm] @ M[pc].T
    for i, mi in enumerate(pm):
        later = pc[(created[pc] > created[mi]) & (sims[i] >= THRESH)]
        if len(later):
            s.reaffirm([ids[mi]], float(created[later].max()))
n_re = s.db.execute("SELECT COUNT(*) c FROM artifacts WHERE last_reaffirmed IS NOT NULL").fetchone()["c"]
print(f"  backfilled {n_re} memories\n", flush=True)

cases = []
for p in PROJECTS:
    cases.extend(build_cases_from_store(s, project=p, holdout_turns=2, min_session_turns=4))
print(f"cases: {len(cases)}\n", flush=True)


def judge(case, reaffirm_on):
    tier = route_query(case.query)
    if tier == Tier.SKIP:
        return Outcome.TIE.value, 0
    @contextmanager
    def maybe_off():
        if reaffirm_on:
            yield
        else:
            with patch.object(SqliteStore, "get_reaffirmed_timestamps", return_value={}):
                yield
    with maybe_off():
        r = recall(case.query, case.scope_project, EVAL, embedder=e, k=tier.k,
                   threshold=0.15, max_tokens=tier.max_tokens, session_id=case.session_id)
    if not r.hit_ids:
        return Outcome.TIE.value, 0
    prompt = COUNTERFACTUAL_PROMPT.format(query=case.query,
        holdout="\n".join(case.holdout_texts), recalled_context=r.formatted_context)
    try:
        return parse_verdict_json(llm.complete(prompt)).outcome.value, r.tokens_injected
    except Exception as ex:
        return f"err:{type(ex).__name__}", r.tokens_injected


base_c, aff_c = Counter(), Counter()
improved = worsened = 0   # McNemar discordant pairs (on loss)
base_tok, aff_tok = [], []
for i, c in enumerate(cases, 1):
    bo, bt = judge(c, False)
    ao, at = judge(c, True)
    base_c[bo] += 1; aff_c[ao] += 1
    if bt: base_tok.append(bt)
    if at: aff_tok.append(at)
    if bo == "loss" and ao in ("tie", "win"):
        improved += 1
    elif bo in ("tie", "win") and ao == "loss":
        worsened += 1
    flag = "  IMPROVED" if (bo=="loss" and ao in ("tie","win")) else ("  WORSENED" if (bo in ("tie","win") and ao=="loss") else "")
    print(f"[{i}/{len(cases)}] base={bo:<5} aff={ao:<5}{flag}", flush=True)


def mcnemar_p(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) * (0.5 ** n) * 2
    return min(p, 1.0)


def summ(name, c, tok):
    w, t, l = c.get("win",0), c.get("tie",0), c.get("loss",0)
    j = w+t+l
    print(f"  {name}: win={w} tie={t} loss={l}  do-no-harm={100*(1-l/j) if j else 0:.1f}%  "
          f"avg_tokens={sum(tok)/len(tok) if tok else 0:.0f}")

print("\n=== reaffirmation powered eval (temp=0, paired) ===")
summ("baseline    ", base_c, base_tok)
summ("reaffirmed  ", aff_c, aff_tok)
print(f"\nMcNemar (loss flips): improved(loss->better)={improved}  worsened(better->loss)={worsened}")
print(f"  two-sided exact p = {mcnemar_p(improved, worsened):.3f}")
print("\nVERDICT: ship if improved>worsened with p<~0.05 and do-no-harm not down; "
      "else shelve (effect below resolution).")
