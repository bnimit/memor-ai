# Distilled-aware retrieval — design

**Date:** 2026-06-15
**Status:** Approved (design), pending implementation plan
**Author:** Nimit Bhandari (with Claude)

## Problem

Memor's retrieval surfaces a helpful prior memory in ~65% of eval cases, but in
~20% of cases a helpful memory **exists in the store and is not surfaced**
(RETRIEVAL_MISS). Separately, *wins* in the counterfactual eval correlate almost
entirely with **distilled memories** (`kind='memory'`) being injected, while
*ties* are dominated by raw `session_chunk`s. The corpus is ~85–90% raw chunks,
so the more-valuable distilled memories get crowded out.

### Evidence (live DB, run 2026-06-14)

Retrieval-gap diagnostic (`scratch_retrieval_gap.py`, n=153, oracle sim ≥0.4):

| Bucket | Count | Share |
|---|---|---|
| RETRIEVED (helpful existed and found) | 99 | 65% |
| RETRIEVAL_MISS (helpful existed, missed) | 30 | 20% |
| VALUE_GAP (no helpful memory existed) | 24 | 16% |

LeverB diagnostic (`scratch_leverb_premise.py`, faithful qwen outcomes):

| Outcome | n | % cases w/ distilled | avg distilled/case | avg raw/case |
|---|---|---|---|---|
| win | 8 | 100% | 1.25 | 2.88 |
| tie | 91 | 52% | 0.74 | 3.79 |
| loss | 25 | 60% | 1.00 | 3.04 |

Corpus composition (active artifacts) is ~10% distilled, e.g. Memorable: 107
distilled vs 986 session_chunk.

> Note: the leverb table's 25 "losses" are a small premise-check subset (sessions
> with stored faithful-qwen outcomes) using a per-case win/tie/loss split. It is
> **not** the authoritative do-no-harm figure. The merge guardrail below uses the
> authoritative counterfactual eval (n=148, do-no-harm 96.6% → loss rate ~3.4%).

### Root cause (confirmed in code)

`SqliteStore.search()` fetches `max(k*20, 200)` KNN candidates ordered by cosine,
then returns only `rows[:k]` (k=8) **before** the retriever's kind/recency/quality
blend runs. The kind weight (`KIND_WEIGHTS[memory]=1.3`, `kind_weight=0.15`) that
is supposed to favor distilled memories therefore only ever operates on 8
already-cosine-filtered items. A helpful distilled memory ranked #12 by raw cosine
is discarded at the handoff and can never be promoted. We fetch ~200 candidates
and throw away ~192, including the distilled ones we want.

## Goal

Stop distilled memories from being crowded out by raw session_chunks — at both the
candidate-selection stage and the ranking stage — without regressing do-no-harm.

Non-goals (explicitly out of scope): temporal supersession / staleness handling
(#2), distillation coverage for VALUE_GAP cases, any change to ingestion or
distillation, MCP, or the dashboard.

## Design

Two layers change; no new modules.

### Store layer (`memor/store/sqlite_store.py`)

`search()` and `search_lexical()` return a **widened, kind-stratified candidate
pool** instead of `rows[:k]`:

1. **Widen the KNN fetch** from `max(k*20, 200)` to `max(k*40, 400)` so distilled
   memories are well-represented in the raw candidate pool even when chunks
   dominate by cosine.
2. **Partition fetched rows into `memory` vs non-`memory`** and return up to
   `pool_per_kind` (default **20**) of each. Distilled candidates are guaranteed
   present whenever ≥1 distilled item is in the KNN fetch, regardless of how many
   chunks outrank it by cosine. Same partition logic in `search_lexical()` (BM25).
3. Existing project/active/since/until/`scope.kinds` filters are preserved
   unchanged. When `scope.kinds` restricts kinds, partitioning respects it.

A new `pool_per_kind` (and the widened `knn_fetch`) are parameters with the
defaults above; callers may override.

### Retriever layer (`memor/retrieve/retriever.py`)

- Raise `kind_weight` default 0.15 → **0.25** so distilled candidates now present
  in the pool are promoted in the blend. `KIND_WEIGHTS[memory]` stays 1.3 for the
  first eval pass — tuned only if the eval calls for it.
- Add an **optional final-cut distilled floor** `min_distilled_final` (default
  **0 = off**): a backstop that reserves up to N of the final `k` slots for the
  top-scoring distilled memories that pass the similarity gate. Ships disabled;
  enabled only if eval shows candidate-stage stratification + reweight did not
  lift distilled-into-final.
- The existing `min_similarity` cosine gate continues to run on the dense pool
  before fusion, so irrelevant distilled memories (negative cosine) are still
  dropped. This is the do-no-harm guardrail against injecting weak distilled
  memories purely because they are distilled.

### Recall layer (`memor/recall.py`)

No contract change. `recall()` threads the new knobs through to `Retriever`
(`pool_per_kind`, `kind_weight`, `min_distilled_final`) with the defaults above.
Threshold (0.3), token budget (1500), 600-char truncation, same-session
exclusion, and `exclude_ids` all continue to apply downstream unchanged.

### Config knobs (all eval-tunable)

| Knob | Default | Where |
|---|---|---|
| `knn_fetch` | `max(k*40, 400)` | store search |
| `pool_per_kind` | 20 | store search / search_lexical |
| `kind_weight` | 0.25 | retriever blend |
| `min_distilled_final` | 0 (off) | retriever final cut |

## Eval gate (must pass before merge)

Re-run both diagnostics and the authoritative counterfactual eval:

- **Primary (must improve):** RETRIEVAL_MISS drops from ~20% — the helpful memory
  now surfaces.
- **Mechanism check:** avg distilled-per-case rises in the RETRIEVED / win buckets
  (`leverb`).
- **Guardrail (hard, blocks merge):** counterfactual **do-no-harm does not
  regress** — loss rate stays ≤ current (~3.4%). Any increase blocks merge even if
  RETRIEVAL_MISS improves. If boosting distilled raises losses, reject the change
  or tighten `min_similarity`.

## Testing

- Store unit tests: stratified pool returns a distilled item even when N chunks
  rank higher by cosine; widened KNN fetch honored; `pool_per_kind` respected;
  project/active/since/until/`scope.kinds` filters still honored; `search_lexical`
  partitions the same way.
- Retriever unit test: a distilled item present in-pool but low cosine reaches the
  final cut once `kind_weight` is raised; `min_distilled_final` reserves slots when
  enabled and is a no-op at default 0.
- Existing 298 tests stay green.

## Rollout

Single feature branch, single PR. Eval results (before/after RETRIEVAL_MISS, win
rate, loss rate) included in the PR description. Defaults conservative so the
change is measured, not assumed.
