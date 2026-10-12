# Prove-recall campaign — design

**Date:** 2026-09-18  
**Status:** Approved to implement  
**PR:** #47  
**Depends on:** matched ATT meter (`docs/plans/2026-09-17-recall-worth-matched-att-design.md`),
temporal M1 (`docs/plans/2026-09-17-temporal-validity-and-recall-design.md`)

## Problem

Compression is earning its keep. Recall injects tokens and, on Claude matched ATT,
has **not** proven it reduces work (`no_effect`, ATT −15.6% vs MDE 16.9%). We need a
written campaign: tighten inject policy, measure offline + observational gates, and
only then claim “recall works.”

## Goal

Ship a **prove-recall** loop that:

1. Applies a **`strict` inject policy** (fewer, better hits).
2. Runs an **offline A/B** (default vs strict) on recent logged queries — no wait.
3. Records **matched ATT baseline** and optionally **stamps** a forward window.
4. Optionally runs **stratified counterfactual** when an LLM key is present.
5. Writes evidence JSON with an explicit **pass / fail / needs_forward_window** decision.

## Non-goals

- Multi-agent episode parsers.
- Randomized recall holdout (documented as Phase 2).
- Flipping `MEMOR_SUPERSESSION` defaults without stratified clear.
- Reviving shelved 2026-06 “widen KNN” change.

## Architecture

```
                    ┌─────────────────────────────┐
  MEMOR_RECALL_PROFILE=default|strict            │
                    └──────────┬──────────────────┘
                               ▼
  query → Retriever → score/threshold → [strict filters] → format inject
                               │
                               ▼
  memor prove-recall ──► offline A/B on recall_log previews
                     ──► matched ATT snapshot (Claude)
                     ──► optional counterfactual arms
                     ──► docs/eval/YYYY-MM-DD-prove-recall.json
```

### Strict profile (treatment)

Applied in `recall()` after retrieval, before formatting:

| Knob | Default | Strict |
|---|---|---|
| Score floor (hook already passes threshold) | caller (often 0.15) | **max(caller, 0.25)** |
| Max hits after filter | unlimited within token budget | **≤ 4** |
| Prefer distilled | off | If any `kind=memory` in candidates, **drop `session_chunk`** (and `snippet`) |
| Supersession | env as today | When profile=strict for prove arm, set `MEMOR_SUPERSESSION=1` for that process only |

Rationale: matched ATT suggested recall may add work; the cheapest fix is **tax less context** and **prefer decisions over chat echo**.

### Evidence gates

| Gate | How | Pass |
|---|---|---|
| **G0** Meter health | `memor recall-worth` | `match_rate ≥ 0.60`, `n_pairs ≥ 50` (verdict may be null) |
| **G1** Offline thrift | prove-recall offline A/B | Strict mean `tokens_injected` **≤ 85%** of default on queries where default had hits; memory-kind share **≥** default |
| **G2** Forward ROI | After stamp + ≥7d traffic under strict | Matched ATT verdict `saves` **or** honest powered `no_effect` with ATT ≥ 0 |
| **G3** Temporal (optional) | Stratified counterfactual | Dispute-present improves vs baseline by > noise; no-dispute no regress |

**Marketing “recall saves work”** requires **G0 + G2 saves**.  
**Shipping strict as default** requires **G0 + G1** now, and G2 within one forward window (revert if G2 is `costs`).

## Claim scope

Until multi-agent parsers exist: evidence is **Claude Code** for ATT; offline A/B uses whatever agents wrote `recall_log` rows (report `by_agent` if present).

## Decision tree (prove-recall output)

```
if not G0:          → fail_meter (fix measurement, not policy)
elif not G1:        → fail_policy (strict not thrifty / not memory-heavier)
elif G2 not due:    → needs_forward_window (stamp set; keep strict opt-in)
elif G2 saves:      → pass_roi (may default strict + market carefully)
elif G2 no_effect:  → hold (honest null; do not market ROI)
elif G2 costs:      → revert_strict
```

## Surfaces

- Env: `MEMOR_RECALL_PROFILE=default|strict`
- CLI: `memor prove-recall [--stamp] [--project NAME] [--offline-n N] [--skip-counterfactual]`
- Evidence: `docs/eval/YYYY-MM-DD-prove-recall.json`
