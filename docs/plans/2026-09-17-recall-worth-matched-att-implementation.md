# Recall-worth matched ATT Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.

**Goal:** Make `memor recall-worth` headline a matched ATT with treatment hygiene and a written verdict rule (Approach 1′).

**Spec:** `docs/plans/2026-09-17-recall-worth-matched-att-design.md`

## Status

**Meter tasks 1–5 complete** (2026-09-17). Live corpus: powered null → M2 is next as a *fresh design*, not revive of shelved 2026-06 widen. Evidence: `docs/eval/2026-09-17-recall-worth-matched.json`.

---

### Task 1–4: Hygiene, match, verdict, dashboard

- [x] Treatment hygiene (`MIN_RECALL_CHARS`)
- [x] `match_treated_controls` + `matched_att`
- [x] `verdict_from_matched` as headline; strata diagnostic; `400-+` exploratory
- [x] CLI `format_report` + dashboard `/api/recall-worth` UI
- [x] Tests in `tests/test_recall_worth_matched.py` + updated `tests/test_episodes.py`

### Task 5: Local re-run + decision tree

- [x] Live `memor recall-worth`: ATT −15.6%, MDE 16.9%, match_rate 95%, pairs 1476 → `no_effect`
- [x] Evidence JSON written
- [x] Decision: powered null → **fresh M2 design** (memory lane already exists; prior widen shelved)

---

## Done when (meter)

- Matched ATT is the CLI/dashboard headline — **yes**
- Tests green; evidence recorded — **yes**
- M2 only per decision tree — **gated; not auto-implemented**
