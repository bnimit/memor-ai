# Reaffirmation-recency temporal ranking — design

**Date:** 2026-06-16
**Status:** Approved (design), pending implementation plan
**Author:** Nimit Bhandari (with Claude)

## Problem

memor's measured #1 failure mode is **stale memory** — a memory that was true once
but is now wrong gets recalled and misleads the agent (a counterfactual "loss").
Existing handling is partial:

- **Supersession** (`distill/distiller.py`) only fires on an explicit replacement
  cue (`SUPERSEDE_REGEX`: "instead of / no longer / switched from / ripped out /
  replaced…with / deprecated / migrated from / moved away from") above
  `SUPERSEDE_SIM_THRESHOLD = 0.80`. Implicit updates (you just start doing X, no
  cue phrase) are missed.
- **Recency** is already in ranking (`recency_weight=0.25`, exp decay,
  `RECENCY_HALF_LIFE_DAYS=14`) — but it decays by `created_at`, i.e. **age**.

**Age is not validity.** A memory can be old-but-still-true ("auth uses JWT") or
recent-but-wrong. Decaying by raw age punishes old-but-valid memories and is the
trap a naive "more recency weight" change falls into. (We already shelved one
ranking-weight tweak this cycle; this design deliberately avoids adding a weight.)

This is informed by the long-term-memory research sweep (2026-06-16): the
field's durable wins are write-side/temporal (Zep bi-temporal validity, Mem0
supersession), not hot-path ranking weights, and validity must be tracked
separately from creation time.

## Goal

Make genuinely stale memories sink in recall while keeping old-but-still-relevant
memories ranked — by decaying from **last reaffirmation**, not creation time —
without a new ranking weight and without an LLM on the recall hot path.

Non-goals: general contradiction detection / Mem0-style ADD/UPDATE/DELETE/NOOP
(write-side, deferred); bi-temporal knowledge graph (no graph DB); any recall-time
LLM; changes to the distiller's existing cue-based supersession (reused as-is).

## Design

**Core idea:** the recency term decays from
`effective_ts = max(created_at, last_reaffirmed)` instead of `created_at`. A memory
whose content keeps reappearing in new work stays "fresh"; one that goes quiet
decays and sinks. No new blend weight — we change *which timestamp* the existing
recency decay reads.

### Store (`memor/store/sqlite_store.py`)
- Add nullable `last_reaffirmed REAL` to `artifacts` via a `_migrate_*` step
  (mirrors `_migrate_quality_decay` etc.; `NULL` on existing rows).
- `reaffirm(ids, ts)` — set `last_reaffirmed = max(COALESCE(last_reaffirmed,0), ts)`
  for the given ids.
- `get_reaffirmed_timestamps(ids)` — batched reader returning `{id: ts}` for
  candidates (mirrors the batched quality lookup; one query, hot-path-cheap;
  callers default missing to 0).

### Ingest (`memor/daemon.py` / distill path)
A reaffirmation pass over each newly-ingested session's chunks:
1. For a new chunk, find active `kind='memory'` artifacts in the same project with
   cosine ≥ `REAFFIRM_SIM_THRESHOLD` (reuse `store.search` with a kind filter).
2. **Guard:** if the chunk text matches the distiller's `SUPERSEDE_REGEX`, do NOT
   reaffirm — that's a (potential) contradiction; leave it to the existing
   supersession path.
3. Otherwise `reaffirm(matched_memory_ids, chunk.created_at)`.

Cost is ingest-time/background only. `REAFFIRM_SIM_THRESHOLD` is a tunable constant
(start ~0.6; below the 0.80 supersede threshold since reaffirmation is a looser
"same topic, still active" signal).

### Retriever (`memor/retrieve/retriever.py`)
One change to the recency computation:
```
eff_ts = max(a.created_at, reaffirmed.get(a.id, 0.0))
age_days = (now - eff_ts) / 86400
recency = exp(-0.693 * age_days / RECENCY_HALF_LIFE_DAYS)
```
`reaffirmed` is fetched once per query via the batched reader. A `NULL`
(absent) `last_reaffirmed` ⇒ `eff_ts == created_at` ⇒ **identical to today**.

### Recall (`memor/recall.py`)
No contract change; the retriever transparently uses the new timestamp.

## Eval gate (the powered eval)

Run the now-validated local eval, made trustworthy this cycle:
- **Judge at `temperature=0`** — a baseline-vs-baseline rerun agreed 47/48 (98%),
  vs the ±6pp swing at default temperature. Bake temp=0 into the counterfactual
  judge as part of this work.
- **Paired analysis (McNemar)** over the **full** case set (not a per-project cap):
  judge each case under baseline vs reaffirmation, count cases that flip
  loss→tie/win vs tie/win→loss. This is far more powerful than comparing marginal
  loss rates.

Gates:
- **Primary:** stale-memory losses decrease (cases flipping loss→tie/win
  significantly outnumber the reverse, by McNemar).
- **Hard guard:** do-no-harm does not regress; memories with `NULL last_reaffirmed`
  are provably unchanged (regression test).
- **Deterministic check:** recall latency flat (one extra batched column read).

## Testing
- Store: migration adds `last_reaffirmed`; `reaffirm` takes the max (never moves a
  timestamp backward); `get_reaffirmed_timestamps` batched result matches; absent
  id ⇒ not in dict.
- Ingest: a matching non-cue chunk reaffirms the memory; a cue-bearing chunk does
  NOT reaffirm (and still supersedes via the existing path).
- Retriever: a reaffirmed-but-old memory ranks above an equally-similar quiet
  memory with the same `created_at`; a memory with `NULL last_reaffirmed` scores
  identically to the pre-change code (no-regression).
- Existing suite stays green.

## Known residual risk
A contradiction with **no cue word** (static embeddings can't detect the negation)
can still falsely reaffirm a now-wrong memory, keeping it fresh. Accepted for v1;
the do-no-harm gate is the backstop. If it bites, the follow-up is the deferred
Mem0-style contradiction detection at ingest.

## Rollout
Single feature branch (`feat/reaffirmation-recency`), single PR, independent of the
Cursor fix (#34) and the retrieval safe-wins (#35). PR includes the McNemar table
(loss→better vs better→loss counts) and the latency/no-regression checks.
