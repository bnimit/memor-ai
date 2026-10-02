# Temporal validity & recall truth — design

**Date:** 2026-09-17  
**Status:** Approved for implementation (approach B + ship bar amended after gap review)  
**Supersedes / extends:** `docs/superpowers/specs/2026-06-28-temporal-validity-design.md` (gitignored; content absorbed here)  
**Related:** architecture review P5; LongMemEval harness caveats

## Problem

Two failure modes hurt coding-agent memory:

1. **Stale facts win.** A memory that was true (“we use React 17”) is still injected after a later memory updated it. Hard regex supersession exists at distill time but misses most semantic updates; soft temporal signals (`disputes` / `validity`) were started then orphaned (empty table, validity stuck at 1.0).
2. **Distilled memories lose to chunk echo.** Near-duplicate session chunks outrank paraphrased memories. Memory lane already exists; temporal work does not replace it.

This milestone targets **(1) temporal truth** as M1. **(2) recall quality (memory vs chunk)** is M2 and is *not* claimed as delivered by M1.

## Goals

- Type-aware recency half-lives (Component A).
- Local, reversible semantic disputes (Component B): soft validity demotion + hard drop when replacement is co-retrieved.
- Single supersession write path: soft disputes for updates; hard `active=0` only for near-duplicate dedup (≥0.92).
- Detection + backfill always on; recall-time action gated by flags until eval clears.
- Deterministic micro-fixtures in CI; stratified counterfactual as release gate.

## Non-goals

- Profiles, dreaming, hybrid memory/chunk product API.
- Local NLI cross-encoder (upgrade path if dispute precision < 0.80).
- Cross-project supersession.
- Task-outcome (SWE-bench) harness.
- Claiming LongMemEval temporal/knowledge-update gains from M1 (harness does not distill or score fact currency — see Eval).

## Architecture

```
distill / backfill          store                     recall (flagged)
─────────────────          ─────                     ────────────────
new memory M               disputes table            candidate set
  → KNN neighbors          validity derived          → drop O if M co-present
  → band in TRUE cosine    from active disputers     → score *= validity
  → insert dispute         (never mutate directly)   → type-aware recency
  → recompute validity                               (MEMOR_TYPE_HALFLIFE)
                         exact dedup ≥0.92
                         still hard-deactivates
```

**Similarity units.** `store.search` returns `sim = 1 − L2`, not cosine. All dispute bands are defined in **true cosine** via one helper:

```
# unit vectors: L2 = √(2−2cos), stored_sim = 1 − L2
# cos = 1 − L2²/2 = 1 − (1 − stored_sim)²/2
```

Never compare a June-style `[0.80, 0.92)` band to raw store `sim`.

## Components

### A — Per-type recency half-lives

Lookup by `meta.mem_type`, else `kind` (same matrix as June design: decision 90d, session_chunk 14d, …).  
`recency = exp(−0.693 · age_days / half_life)`.  
`decay_quality` uses the same table for `stale_days` per memory.  
Flag: `MEMOR_TYPE_HALFLIFE` (default off).

### B — Soft disputes

**Detection** (always on at distill + backfill):

1. After storing M, take KNN memories in project (reuse dedup neighbors when possible).
2. Keep older, fact-bearing types with **true cosine ∈ [0.80, 0.92)**.
3. Quality guard: `M.quality ≥ O.quality − 0.1` and `M.quality ≥ 0.5`.
4. Insert `(disputed_id=O, disputer_id=M)`; recompute O’s validity.

**Validity:** `0.5 ^ (active_disputer_count)`, floor 0.25. A disputer is inactive if it is itself disputed (transitivity, with cycle guard) or its row is dormant.

**Recall action** (`MEMOR_SUPERSESSION`, default off):

1. If O and any active disputer M are both candidates → drop O.
2. Else multiply score by validity (including edge-expanded hits).

**Recovery:** on confirmed **use** (`use_count` increment), bump affirmations on newest active dispute for O; at ≥2 → dormant → recompute validity. If live use-signal coverage is near zero, recovery is best-effort — not a ship-blocking safety property. Measure coverage once before relying on it in docs/dashboard claims.

### Dedup vs dispute (single write path)

| Store `sim` / true cos | Action |
|---|---|
| true cos ≥ 0.92 | Exact dedup: do not store M (or hard-deactivate duplicate) — **unchanged** |
| true cos ∈ [0.80, 0.92) + age/quality gates | Soft dispute only — **no** `active=0` |
| Regex `_REPLACEMENT_RE` / LLM `supersedes_text` | Converted to dispute writer (same table), not hard deactivate |

## Schema

- `memory_quality.validity REAL DEFAULT 1.0` (migrate if missing; tolerate orphaned column).
- `disputes(disputed_id, disputer_id, created_at, affirmations, dormant)` PK `(disputed_id, disputer_id)`.
- `meta.disputes_backfilled`.
- Backfill: **KNN per active memory**, not full pairwise. Resumable; daemon once via meta flag; CLI `memor backfill-disputes`.

## Feature flags

| Flag | Default | Effect |
|---|---|---|
| `MEMOR_TYPE_HALFLIFE` | off | Per-type recency + type-aware quality decay |
| `MEMOR_SUPERSESSION` | off | Validity multiply + co-retrieve drop |

Detection and backfill run regardless so flipping flags takes effect without re-scan.

## Eval / ship bar (amended)

**Hard gates (M1b flag flip):**

1. **Supersession micro-fixtures** (~15 pairs) in pytest: replacement ranks above stale; stale dropped when both candidates. Runs on every PR.
2. **Stratified counterfactual** (release/nightly): dispute-present stratum clears ±6pp noise floor vs baseline; no-dispute stratum does not regress. Dispute precision sample ≥ 0.80 (temp=0 judge).

**Non-gates for M1:**

- LongMemEval overall any-hit / all-gold: smoke non-regression only. The harness indexes chunks only and scores session-id membership — it cannot credit Component B until a separate arm exists.
- Use-based recovery efficacy.

**Evidence artifact:** checked-in JSON under `docs/eval/` (or `memor/eval/results/`) with date, commit, metrics — not a chat claim.

## Phasing

| Phase | Deliverable |
|---|---|
| **M1a** | Schema, cosine helper, dispute detect/recompute, distill+backfill write path, flags off, unit tests + micro-fixture scaffold |
| **M1b** | Wire recall scoring + type-aware half-lives behind flags; run eval; flip defaults only if bars clear |
| **M2** | Memory-vs-chunk recall quality (lane/ranking). Separate milestone; rename keeps M1 honest as “temporal truth” |

## Risks

- False-positive disputes → bounded by soft default, co-retrieve hard-drop, quality gate, default-off flags.
- Backfill on large projects → KNN-only + progress logging.
- Orphan schema → migrations must be idempotent when table/column already exist empty.

## Files (expected)

| File | Role |
|---|---|
| `memor/retrieve/similarity.py` | `stored_sim_to_cosine` / band checks |
| `memor/supersession.py` | detect, recompute validity, candidate drop helpers |
| `memor/store/sqlite_store.py` | schema, disputes CRUD, get_quality+validity batch |
| `memor/distill/distiller.py` | dispute after store; stop hard-deactivate on update cues |
| `memor/retrieve/retriever.py` | half-life, validity, drop (flagged) |
| `memor/daemon.py` | one-shot backfill; type-aware decay when A on |
| `memor/cli.py` | `backfill-disputes` |
| `tests/test_supersession*.py` | unit + micro-fixtures |
