# Distilled-aware retrieval — design

**Date:** 2026-06-15 (revised after miss autopsy)
**Status:** Implemented, then **SHELVED after eval** (2026-06-16). See Outcome below.
**Author:** Nimit Bhandari (with Claude)

## Outcome (2026-06-16) — shelved on eval

The widen + kind-stratify + reweight change was implemented and measured. It did
**not** earn its way in:

- The no-LLM cosine proxy showed it *regressing* RETRIEVAL_MISS — but that proxy
  rewards pure cosine and penalizes the recency/kind blend, so it was the wrong
  target.
- The counterfactual A/B (qwen-14b judge, n=77) first looked positive
  (do-no-harm 81.8%→89.5%) but a token-budget sweep exposed the signal as **judge
  noise**: the *identical baseline config* swung 81.8%↔88.2% do-no-harm
  (loss 14↔9) across two runs. Every widen-vs-baseline delta (Δ2–6 losses) sits
  inside that noise floor. n=77 with a stochastic local judge cannot rank these
  configs.
- The only deterministic effect was **token cost**, which widening *increased*
  (646→1131 avg injected, uncapped).

Decision: do not ship the behavior change on a noise-level signal. **Kept only the
two unambiguous wins** (batched `get_quality_scores`, KNN-fetch cap); reverted
widen/stratify/reweight. Properly resolving widening would need a much larger,
multi-seed eval and a less noisy judge — revisit only if a better-evidenced method
emerges (e.g. from the long-term-memory research).

The original design is preserved below for the record.

---

## Problem

Memor's retrieval fails to surface a helpful prior memory that demonstrably
exists in the store in a meaningful fraction of cases (RETRIEVAL_MISS).
Separately, *wins* in the counterfactual eval correlate almost entirely with
**distilled memories** (`kind='memory'`) being injected, while *ties* are
dominated by raw `session_chunk`s. The corpus is ~85–90% raw chunks.

### Evidence — miss autopsy (live DB, 2026-06-15)

`scratch_miss_autopsy.py` traces, for each miss, where the helpful memory fell
out of the pipeline. Helpful = top in-project artifact by **pure cosine** to the
holdout (independent of the blended Retriever we change — see Eval gate). n=165
cases, **77 misses**:

| Bucket | Count | Share | Meaning |
|---|---|---|---|
| **KNN_TRUNCATED** | 47 | **61%** | helpful in-project cosine-rank ~9–60, cut by `rows[:8]` |
| **ABSENT_FROM_KNN** | 23 | 30% | global cosine-rank ≫ 200 (and in-project rank also deep) |
| **CANDIDATE_DROPPED** | 7 | 9% | was in the top-8 candidates yet not returned |
| LOW_SIM_TO_QUERY | 0 | 0% | — |

Key reading of the per-miss detail:

- **The dominant cause (61%) is plain truncation at the `rows[:k]` handoff, not
  kind crowd-out and not project scoping.** In KNN_TRUNCATED, global rank ≈
  in-project rank everywhere (e.g. 13/13, 24/24, 33/33) at high similarity
  (0.77–0.90) — the helpful item is just past the top-8 cutoff. Returning more
  than 8 candidates recovers these; project-scoped KNN does nothing for them.
- **Project-scoped KNN is not worth it.** ABSENT cases have deep *in-project*
  ranks too (214, 326, 499, 557, 1255…); a 200-deep project KNN would recover
  only ~5 of 77 misses (~6.5%). Widening the *global* fetch catches the few with
  global rank in the low hundreds far more cheaply.

### Why distilled still matters (leverb diagnostic)

| Outcome | n | % cases w/ distilled | avg distilled/case | avg raw/case |
|---|---|---|---|---|
| win | 8 | 100% | 1.25 | 2.88 |
| tie | 91 | 52% | 0.74 | 3.79 |
| loss | 25 | 60% | 1.00 | 3.04 |

Wins carry the most distilled and fewest raw chunks; ties the reverse. So once
the pool is widened, biasing the *ranking* toward distilled memories is the lever
most associated with converting ties → wins. (n=8 wins — directional only; the
counterfactual n=148 is the real gate.)

> Note: the leverb "losses" are a small premise-check subset, **not** the
> authoritative do-no-harm figure (counterfactual n=148, do-no-harm 96.6% → loss
> rate ~3.4%), which the merge guardrail uses.

### Root cause (confirmed in code)

`SqliteStore.search()` fetches `max(k*20, 200)` KNN candidates ordered by cosine,
then returns only `rows[:k]` (k=8) **before** the retriever's kind/recency/quality
blend runs. So the blend — including the kind weight meant to favor distilled
memories — only ever sees 8 already-cosine-truncated items. The autopsy confirms
the helpful memory is typically rank ~9–60: fetched, then discarded at the
handoff.

## Goal

Recover the helpful memories the store already holds — primarily by not
discarding the fetched candidate pool — and bias ranking toward distilled
memories, without regressing do-no-harm.

Non-goals (explicitly out of scope, with reasons):
- **Project-scoped KNN** — autopsy shows ≤6.5% payoff for high complexity.
- Temporal supersession / staleness (#2); distillation coverage for VALUE_GAP;
  ingestion/distillation changes; MCP.

## Design

Two layers change; no new modules. Ordered by measured leverage.

### 1. Widen the candidate handoff — primary fix (store)

`SqliteStore.search()` / `search_lexical()` return a wider pool instead of
`rows[:k]`:
- Return up to `candidate_pool` (default **128**) candidates to the retriever,
  which then blends and cuts to the final `k`. Recovers the 61% KNN_TRUNCATED
  bucket: observed in-project ranks there run ~9–130, so 128 covers ~45 of 47;
  the ablation tunes the exact value against latency.
- Widen the KNN fetch from `max(k*20, 200)` to `max(k*40, 1000)` (capped at
  sqlite-vec's 4096 limit) to also catch ABSENT cases whose global rank is in the
  low hundreds.
- **Batch the quality lookup.** The retriever currently calls
  `get_quality_score()` per candidate (N+1 queries); with a 128-wide pool that
  is the main latency risk. Replace it with a single `get_quality_scores(ids)`
  query over the candidate set so a wider pool stays within the hook's <15ms
  budget. This is a prerequisite for widening, not an optional extra.
- Existing project/active/since/until/`scope.kinds` filters unchanged.

### 2. Kind-stratify + reweight — secondary (store + retriever)

- In the widened pool, guarantee distilled (`kind='memory'`) representation:
  partition the fetched rows and keep up to `pool_per_kind` (default **64**) of
  `memory` and of non-`memory`, so distilled candidates are present whenever ≥1
  is in the fetch. (With the pool widened to 128, this mostly matters for projects
  where chunks dominate the near-neighbors.)
- Raise `kind_weight` 0.15 → **0.25** so distilled candidates in the pool are
  promoted. `KIND_WEIGHTS[memory]` stays 1.3 for the first pass.
- The `min_similarity` cosine gate continues to run on the dense pool before
  fusion, so weakly-relevant distilled memories are still dropped — the do-harm
  guardrail. Because widening (not reweighting) is now the primary lever, the
  reweight is deliberately modest to limit do-harm risk.

### 3. CANDIDATE_DROPPED follow-up (investigate, not pre-built)

The 9% that were top-8 yet dropped (sim 0.82–0.93) are most likely token-budget
eviction or blend demotion. The implementation plan includes a short
investigation; a fix only ships if the cause is clear and low-risk. No
speculative knob.

### Recall layer (`memor/recall.py`)

No contract change. `recall()` threads the new knobs (`candidate_pool`,
`pool_per_kind`, `kind_weight`) to `Retriever`. Threshold (0.3 / hook 0.15),
token budget (1500), 600-char truncation, same-session exclusion, `exclude_ids`
all apply downstream unchanged.

### Config knobs (all eval-tunable)

| Knob | Default | Where |
|---|---|---|
| `knn_fetch` | `min(max(k*40, 1000), 4096)` | store search |
| `candidate_pool` | 128 | store search → retriever |
| `pool_per_kind` | 64 | store search / search_lexical |
| `kind_weight` | 0.25 | retriever blend |

(`min_distilled_final` from the prior draft is **dropped** — widening makes a
final-cut floor unnecessary; YAGNI.)

## Eval gate (must pass before merge)

The diagnostics must be reproducible, so they move into `memor/eval/` (out of
`scratch_*.py`) and pin `threshold=0.15` to match the hook. This is a **local**
gate run against the developer corpus, not CI (it needs a populated DB).

- **Frozen oracle (fixes a methodology bug):** "helpful memory exists" is
  computed by **pure cosine** (`store.search` dense, no blend) and snapshotted
  **once before** the change. The same frozen oracle scores both the before and
  after runs, so changing the Retriever cannot move the denominator.
- **Primary (must improve):** RETRIEVAL_MISS drops, driven by the KNN_TRUNCATED
  bucket shrinking.
- **Mechanism check:** avg distilled-per-case rises in the RETRIEVED / win
  buckets (leverb). Directional (n=8 wins).
- **Latency guardrail (hard):** recall p50/p95 must stay within budget — the hook
  runs on every prompt (<15ms target). The widened pool adds blend work and
  per-candidate quality lookups; measure and cap.
- **Do-no-harm guardrail (hard):** counterfactual loss rate stays ≤ current
  (~3.4%). Any increase blocks merge.
- **Ablation:** report the metrics for baseline → +widen → +stratify → +reweight
  so each knob's contribution is attributable.

## Testing

- Store unit tests: widened pool returns a candidate at cosine-rank > k (the
  truncation fix); `candidate_pool` / `pool_per_kind` honored; stratified pool
  returns a distilled item even when N chunks rank higher; project/active/
  since/until/`scope.kinds` filters still honored; `search_lexical` matches.
- Retriever unit test: a candidate present in-pool but below the old top-8 cosine
  rank reaches the final cut; distilled item with low cosine promoted once
  `kind_weight` is raised.
- Store unit test: `get_quality_scores(ids)` returns the same scores as the old
  per-id `get_quality_score()` in one query (and 0.5 default for unknown ids).
- Existing 298 tests stay green; new diagnostics importable from `memor/eval/`.

## Rollout

Single feature branch (`feat/distilled-aware-retrieval`), single PR, separate
from the Cursor fix (#34). PR description includes the ablation table
(before/after RETRIEVAL_MISS, distilled-per-win, loss rate, latency). Conservative
defaults so the change is measured, not assumed.
