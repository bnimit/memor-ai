# Reaffirmation-recency temporal ranking — design

**Date:** 2026-06-16
**Status:** Implemented, then **SHELVED on eval** (2026-06-17). Not merged. See Outcome.
**Author:** Nimit Bhandari (with Claude)

## Outcome (2026-06-17) — shelved: net-negative on the powered eval

Built fully (store `last_reaffirmed` + `reaffirm`/reader, ingest reaffirmation
pass, retriever `effective_ts`; 313 tests green) and run through the powered eval
(temp=0 judge, paired, on a backfilled copy of the live corpus, ~175 cases).

Result: the loss-flips ran **net-negative** — roughly **1 improved (loss→better)
vs 4 worsened (better→loss)** at ~70% through (final McNemar appended below once
the run closes). Every WORSENED was a `tie → loss`: reaffirmation lifted a
stale/contradicted memory into a recall slot the judge ruled misleading —
exactly the **cue-less-contradiction / migration-chatter false-reaffirm** risk
this spec flagged as the residual danger.

Decision: **do not ship.** The feature is left unmerged (local branch
`feat/reaffirmation-recency`); `main` is untouched. This is the third recall/
ranking change to fail its eval gate (after widening and λ-fusion), reinforcing
that the bottleneck is not recall ranking — the next experiments move to recall
*matching* (ingest keyphrase enrichment) and a strong-signal reranker
(FlashRank, latency-gated), and ultimately write-side quality.

> Final McNemar (appended on run completion): _pending_

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

**Rescue old-but-still-relevant memories** from age decay — keep them ranked
when their content keeps reappearing in new work — by decaying recency from
**last reaffirmation** instead of creation time, without a new ranking weight and
without an LLM on the recall hot path.

> Honest scoping (corrected after the premise check): `max(created_at,
> last_reaffirmed)` can only move a timestamp *forward*, so it **boosts reaffirmed
> memories; it does not sink un-reaffirmed ones** (a stale memory keeps decaying
> exactly as today). The do-no-harm benefit is therefore *indirect*: a rescued
> valid memory can out-rank a stale one for a top-k slot. This is a "rescue valid"
> change, not a "sink stale" change. Sinking stale memories would require a
> staleness *penalty* (effective age = time since last reaffirmation), which
> carries more do-harm risk and is deferred.

Non-goals: general contradiction detection / Mem0-style ADD/UPDATE/DELETE/NOOP
(write-side, deferred); a staleness penalty (boost-only for v1); bi-temporal
knowledge graph (no graph DB); any recall-time LLM; changes to the distiller's
existing cue-based supersession (reused as-is).

## Effect-size evidence (premise check, 2026-06-16)

`scratch_reaffirm_premise.py` simulated `last_reaffirmed` over the live corpus
(11,694 active artifacts; 1,293 memories) to confirm the signal isn't inert:

| reaffirm thresh | memories reaffirmed | "rescued" (old created + fresh reaffirm) |
|---|---|---|
| 0.6 | 1168/1293 (90%) | 318 (25%) |
| 0.7 | 1058/1293 (82%) | 260 (20%) |
| **0.8** | **616/1293 (48%)** | **105 (8%)** |

Effect size at 0.6: **23/173 eval cases (13%)** had a "rescued" memory already in
the candidate set (i.e. cases the change could plausibly move).

Conclusions that shaped this spec:
- **Threshold must be 0.8, not 0.6.** At 0.6, 90% of memories are "reaffirmed" —
  the signal is non-discriminative. 0.8 (matching `SUPERSEDE_SIM_THRESHOLD`) gives
  a meaningful 48%/8% split.
- **The effect is modest** — ~13% case-touch at the loose threshold, lower (~4–6%)
  at 0.8. It's live (unlike the shelved widening) but near the lower edge of what
  the eval can resolve, so the powered eval is a real gate, not a formality.

## Design

**Core idea:** the recency term decays from
`effective_ts = max(created_at, last_reaffirmed)` instead of `created_at`. A memory
whose content keeps reappearing in new work stays "fresh" (boosted); one that goes
quiet decays as today (not penalised — see Goal). No new blend weight — we change
*which timestamp* the existing recency decay reads. (Effect is bounded by
`recency_weight=0.25`: a full freshness boost shifts a memory's blended score by at
most ~0.25, so this re-ranks among near-similar candidates, it doesn't override
similarity.)

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
   cosine ≥ `REAFFIRM_SIM_THRESHOLD` (reuse `store.search` with `scope.kinds={'memory'}`).
2. **Guard:** if the chunk text matches the distiller's `_REPLACEMENT_RE`, do NOT
   reaffirm — that's a (potential) contradiction; leave it to the existing
   supersession path.
3. Otherwise `reaffirm(matched_memory_ids, chunk.created_at)`.

- **Threshold:** `REAFFIRM_SIM_THRESHOLD = 0.80` — set from the premise check: at
  0.6, 90% of memories get reaffirmed (non-discriminative); 0.80 gives a meaningful
  48% split and matches `SUPERSEDE_SIM_THRESHOLD`.
- **Cost bound:** this is a per-chunk vec search over project *memories* only
  (~1.3k total, hundreds per project), background in the daemon. To bound it, run
  the pass per *session* (batch the session's new chunk vectors, dedupe matched
  memory-ids, one `reaffirm()` write) rather than per-chunk-per-commit. Only new
  chunks from the session being ingested are scanned, so it does not re-scan
  history.

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
- **Primary:** cases flipping loss→tie/win outnumber the reverse (McNemar).
- **Hard guard:** do-no-harm does not regress; memories with `NULL last_reaffirmed`
  are provably unchanged (regression test).
- **Deterministic check:** recall latency flat (one extra batched column read).
- **Shelve criterion (explicit):** the premise check showed the effect is modest
  (~4–6% of cases touched at thresh 0.80). If the powered eval shows **no
  significant favorable flip** (discordant pairs too few, or McNemar p not
  significant), **shelve it** — do not ship on faith. We've already learned this
  cycle that a clean mechanism with a sub-resolution effect isn't worth shipping.
  If the boost-only version is inert, the documented next option is the staleness
  *penalty* variant (re-run the premise check for it first).

## Reconciliation with existing decay (avoid double-counting)

memor already has `decay_quality` (halves quality_score for un-recalled memories)
and `deactivate_stale(days)` (age-based hard deactivation). Those key off **recall**
activity; reaffirmation keys off **new-content** activity — different signals, so
they are complementary, not redundant. For v1 they stay independent: reaffirmation
only feeds the recency term and does **not** reset `quality_score` or
`last_recalled`. (A future option: let a reaffirmation also bump `last_recalled` so
a reaffirmed memory escapes quality decay too — deferred until the recency-only
version is measured, to keep one change at a time.)

A reaffirmed memory's `effective_ts` can stay ~now indefinitely if it keeps being
reaffirmed (no decay cap). That is intended — a continuously-reaffirmed memory is,
by this signal, continuously relevant. Hard contradictions are still handled by the
existing cue-based supersession (which deactivates, removing it from recall
entirely).

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
