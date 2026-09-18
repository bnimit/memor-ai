# Temporal Validity (M1a–M1b) Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax. Follow TDD: failing test → implement → pass → commit.

**Goal:** Ship soft temporal disputes + type-aware half-lives so stale facts stop winning recall, with action gated until eval clears.

**Architecture:** Detect disputes at distill/backfill using true-cosine bands; store in `disputes` with derived `validity`; apply drop + multiply at recall only when `MEMOR_SUPERSESSION=1`. Type-aware recency behind `MEMOR_TYPE_HALFLIFE`.

**Tech Stack:** Python, SqliteStore, existing Retriever / Distiller, pytest.

**Spec:** `docs/plans/2026-09-17-temporal-validity-and-recall-design.md`

## Global Constraints

- Dispute bands use **true cosine**, never raw `1−L2` store sim.
- Hard `active=0` only for dedup (≥0.92 true cos); updates write disputes only.
- Flags default **off**; detection always on.
- Micro-fixtures must pass in CI; LongMemEval is non-regression only.
- Do not claim M2 (memory lane) in this plan.

---

## File map

| File | Responsibility |
|---|---|
| `memor/retrieve/similarity.py` | L2-store-sim ↔ cosine conversion |
| `memor/supersession.py` | Dispute detection, validity recompute, candidate drop |
| `memor/store/sqlite_store.py` | Schema + CRUD + batched quality/validity |
| `memor/distill/distiller.py` | Call detect after store; remove hard update-deactivate |
| `memor/retrieve/retriever.py` | Half-lives + validity scoring when flagged |
| `memor/daemon.py` | Backfill once; type-aware decay when flagged |
| `memor/cli.py` | `backfill-disputes` |
| `tests/test_similarity_cosine.py` | Conversion math |
| `tests/test_supersession_disputes.py` | Detection, validity, drop, recovery |
| `tests/test_supersession_fixtures.py` | Hand-authored stale/replacement pairs |
| `tests/test_type_halflife.py` | Half-life matrix + flag off = unchanged |

---

### Task 1: Cosine helper

**Files:** `memor/retrieve/similarity.py`, `tests/test_similarity_cosine.py`

- [x] Write tests: orthogonal unit vectors → stored_sim ≈ 1−√2 → cos ≈ 0; identical → cos ≈ 1; round-trip band edges 0.80 and 0.92.
- [x] Implement `stored_sim_to_cosine(sim: float) -> float` and `cosine_in_dispute_band(sim: float) -> bool`.
- [x] Run `pytest tests/test_similarity_cosine.py` — pass.
- [x] Commit.

### Task 2: Schema + disputes CRUD

**Files:** `memor/store/sqlite_store.py`, `tests/test_supersession_disputes.py`

- [x] Write tests: open store → `disputes` table exists; `validity` column on `memory_quality`; insert dispute; `recompute_validity`; idempotent migration when table already exists.
- [x] Add `_migrate_disputes_and_validity`, `add_dispute`, `list_active_disputers`, `recompute_validity`, extend `get_quality_scores` to optionally return validity (or new `get_quality_and_validity`).
- [x] Run tests — pass.
- [x] Commit.

### Task 3: Detection logic

**Files:** `memor/supersession.py`, tests

- [x] Write tests: band edges, older-only, fact-bearing gate, quality guard, no hard deactivate, transitivity + cycle guard.
- [x] Implement `find_and_record_disputes(store, embedder, memory_id, ...)`.
- [x] Run tests — pass.
- [x] Commit.

### Task 4: Distill write path

**Files:** `memor/distill/distiller.py`, `tests/test_supersession.py` (update)

- [x] Failing test: replacement cue similar memory → dispute row, **both stay active**.
- [x] Remove hard `deactivate` on `_REPLACEMENT_RE` / `supersedes_text`; call `find_and_record_disputes` after store.
- [x] Keep ≥0.92 dedup as no-store / skip.
- [x] Run distill/supersession tests — pass.
- [x] Commit.

### Task 5: Backfill

**Files:** `memor/store/sqlite_store.py` or `memor/supersession.py`, `memor/cli.py`, `memor/daemon.py`

- [x] Test: seed 3 memories with known dispute pair → `backfill_disputes` creates row; second run idempotent; meta flag set.
- [x] Implement KNN-per-memory backfill + `memor backfill-disputes` + daemon one-shot.
- [x] Commit.

### Task 6: Recall scoring (flagged)

**Files:** `memor/retrieve/retriever.py`, tests

- [x] Test flag off: scores identical to baseline (validity forced 1.0, no drop).
- [x] Test flag on: co-present O+M drops O; lone O gets validity multiply; edge hits get validity.
- [x] Implement behind `MEMOR_SUPERSESSION`.
- [x] Commit.

### Task 7: Type-aware half-lives (flagged)

**Files:** `memor/retrieve/retriever.py`, `memor/store/sqlite_store.py` (`decay_quality`), `tests/test_type_halflife.py`

- [x] Test matrix lookup; flag off → uniform 14d behavior.
- [x] Implement `half_life(artifact)` + wire recency; type-aware decay when `MEMOR_TYPE_HALFLIFE`.
- [x] Commit.

### Task 8: Micro-fixtures CI

**Files:** `tests/test_supersession_fixtures.py`, fixture JSON if needed

- [x] Author ≥10 (target 15) (stale, replacement, query) triples.
- [x] Assert with flags on: replacement in hits, stale dropped when both would qualify.
- [x] Commit.

### Task 9: Eval harness hooks (M1b prep)

**Files:** `memor/eval/` as needed

- [x] Wire dispute-present stratum tagging for counterfactual (can be stub reporting counts).
- [x] Document how to run release eval and where to write the evidence JSON.
- [x] Do **not** flip flag defaults in this task.
- [x] Commit.

### Task 10: M1b gate (separate PR after evidence)

- [x] Run micro-fixtures + stratified eval on a real/project corpus.
- [ ] If bars clear, PR to default flags on (or document opt-in) with evidence JSON.
- [x] If not, keep defaults off; file precision failures for NLI upgrade path.

Evidence: `docs/eval/2026-09-17-temporal-validity-m1b.json` — micro-fixtures 15/15 pass; stratified counterfactual **not_run** (needs corpus + LLM); **decision: keep_flags_off**. Flag-default flip remains a follow-up PR after stratified bars clear.

---

## Done when

- M1a merged: detection + backfill live, flags off, CI green including micro-fixtures scaffold.
- M1b: recall path implements flagged behavior; evidence recorded before any default change.
- Design doc’s non-goals respected (no LongMemEval credit claim, no M2 scope creep).
