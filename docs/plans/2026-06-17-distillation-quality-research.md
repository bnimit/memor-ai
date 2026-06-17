# Write-side (distillation) quality — research patterns & testable hypotheses

**Date:** 2026-06-17
**Status:** Research synthesized; hypotheses pending sequential eval-gated tests
**Author:** Nimit Bhandari (with Claude)

## Why we're here

Five recall-side experiments (widening, λ-fusion, reaffirmation, FlashRank,
keyphrase-enrichment) all came back sub-resolution or net-negative — recall is
near its ceiling on this corpus (~65% helpful-memory retrieved, ~96.6%
do-no-harm). The diagnosed gap is **write-side**: memories are often too
terse/vague to be matched by weak queries (~65% of misses are query-mismatch),
plus coverage gaps and stale memories. This documents a focused distillation
research sweep (deep-research, 22 sources, 23 confirmed / 2 refuted claims) and
turns it into testable hypotheses.

**Hard caveat up front:** every quantitative gain below is author-self-reported on
**conversational** benchmarks (LoCoMo / LongMemEval / Smallville), **not** coding
sessions. Treat all numbers as *motivation to test*, never as predicted results.
The temp=0 paired counterfactual on real coding sessions is the only arbiter. And
the single biggest open risk is whether a **local qwen-14b** distiller is good
enough to realize gains the papers got with GPT-4o-mini.

## Patterns (verified)

### SELECTION — what deserves to become a memory
- **Prediction-error / surprise** (Nemori, 2508.03341): store only what *deviates
  from or extends* what existing memory already predicts. +25% rel (gpt-4o-mini),
  +14.4% (gpt-4.1-mini) over direct distillation on LoCoMo. Governs the *semantic*
  layer only. **Fit: Hard** (multiple ingest LLM calls; quality-sensitive).
- **Importance/poignancy scalar** (Generative Agents, 2304.03442): LLM rates each
  memory 1–10 once at ingest, stored as a static scalar. **Fit: Easy** (LLM at
  ingest, scalar at recall).

### REPRESENTATION — how to write memories so weak queries match (our #1 gap)
- **Enriched embedding** (A-MEM, 2502.12110): embed `concat(content, keywords,
  tags, contextual-description)`, *not* the raw body. Ablation: removing
  enrich+link dropped multi-hop F1 45.85 → 24.55. **Fit: Easy** and **highest-leverage
  for our diagnosed gap.**
- **Self-contained NL rewriting** (Mem0, 2504.19413): rewrite chunks into dense,
  context-complete facts (resolve pronouns/implicit subjects); graph structure
  added little. **Fit: Easy.**
- **Narrative episodes** (Nemori): for decision/lesson types, a short narrative
  (what happened / why / outcome) + cue + raw provenance, vs a one-line fact.
  **Fit: Moderate.** (NOTE: Nemori motivates narratives via memory-granularity,
  *not* weak-query retrievability — that link is our hypothesis.)
- **Decouple embedded surface from injected body** (Affordable Generative Agents,
  2402.02053, *medium* confidence): keyword-tagged compact summaries matched raw
  at ~5% of tokens → we can enrich the *searchable* surface (keywords/tags) without
  bloating the *injected* body. **Fit: Easy.**

### CONSOLIDATION / CONFLICT
- **Retrieve-then-decide ADD/UPDATE/DELETE/NOOP at ingest** (Mem0): for each new
  candidate, retrieve top-s similar existing memories and let the LLM pick the op —
  no separate classifier. Addresses dedup + stale/contradicted memories
  (do-harm / temporal validity). **Fit: Moderate.** (Implement the paper's design;
  the OSS repo reportedly ships an ADD-only shortcut.)

### COVERAGE / ABSTRACTION
- **Reflection / synthesis** (Generative Agents): periodically synthesize
  higher-level lessons (with source provenance) from clusters of recent memories;
  a general lesson can match a weak query no single raw memory matches. Runs async
  at ingest. **Fit: Moderate.**

### RECALL MECHANISM (recall-cheap; stored scalars only)
- **Additive recency + importance + relevance** (Generative Agents): equal-weight
  normalized sum; a high *importance* scalar can surface a low-embedding-similarity
  item. Add a stored importance term to memor's hybrid — zero recall-time LLM.
  **Fit: Easy.** (Distinct from the failed *recency* reaffirmation — importance is
  a different, ingest-generated signal.)

## Refuted — do NOT build on faith
- Nemori embeds cue+narrative concatenation (vote 1-2; embedding strategy unverified).
- A learned cross-attention ranker beats the additive scalar ranker (vote 1-2) —
  stick with the additive scalar combination (confirmed + recall-cheap).

## Testable hypotheses (priority order)

Each is gated by re-distilling a slice of real sessions with the **local LLM**,
then measuring on the **temp=0 paired counterfactual** (win/tie/loss, do-no-harm)
plus the candidacy/rescue diagnostic.

**H1 — Enriched embedding (A-MEM). [Easy · top priority]**
Distill each memory with LLM-generated keywords/tags/contextual-description; embed
the *concatenation*. Hypothesis: lifts weak-query matches → fewer query-mismatch
misses → higher win-rate, no do-no-harm regression. *Directly targets the #1 gap,
and the prior enrichment premise check already showed a 23% candidacy ceiling
(full-source) vs only 6–9% for no-LLM keyphrases — LLM enrichment should recover
much more of that ceiling.* **Test first.**

**H2 — Self-contained NL rewriting (Mem0). [Easy]**
Re-distill memories as standalone, context-complete sentences (no dangling
pronouns/refs). Hypothesis: improves both embedding and BM25 match. Overlaps with
H1; test as an ablation arm.

**H3 — Importance scalar in ranking (Generative Agents). [Easy]**
Emit a 1–10 importance at ingest; add as a stored additive term in the hybrid.
Hypothesis: high-importance memories surface under weak queries. Recall-cheap.
(Watch do-no-harm — boosting by importance can inject confidently-wrong memories.)

**H4 — Mem0 ADD/UPDATE/DELETE/NOOP consolidation. [Moderate]**
Add an ingest update phase that dedups/supersedes against top-s similar memories.
Hypothesis: removes stale/contradicted memories → do-no-harm gain (the temporal-
validity failure reaffirmation tried and failed to fix from the recall side).

**H5 — Reflection/synthesis for coverage. [Moderate]**
Periodic local-LLM pass emitting general lessons with provenance. Hypothesis:
fills VALUE_GAP (~16% of cases where no helpful memory existed).

**H6 — Prediction-error selection (Nemori). [Hard]**
Only store a memory if it contradicts/extends existing ones. Hypothesis: less
redundancy, sharper store. Highest LLM cost + quality sensitivity; test last.

## Recommended sequence
1. **Validate the local distiller first** (the #1 risk): re-distill a small slice
   with qwen-14b under H1 and eyeball quality before trusting any metric.
2. **H1** (enriched embedding) — biggest leverage on the diagnosed gap, lowest risk.
3. Then H2/H3 as cheap ablation arms; H4 (do-no-harm lever); H5/H6 if warranted.
Every step behind the temp=0 paired eval; ship only what clears it; shelve the rest
(as we did with the five recall experiments).

## Sources (primary unless noted)
Nemori 2508.03341 · A-MEM 2502.12110 · Mem0 2504.19413 · Generative Agents
2304.03442 · Affordable Generative Agents 2402.02053 · coding-specific leads
(unverified, budget-dropped): 2411.13941, 2506.06698, 2508.06433, 2510.16079.
