# Prove-recall Implementation Plan

> **For agentic workers:** TDD. Checkbox tracking.

**Goal:** Strict recall profile + `memor prove-recall` (G0/G1 evidence).

**Spec:** `docs/plans/2026-09-18-prove-recall-design.md`

## Status (2026-09-18)

Live run: **G0 pass**, **G1 pass** (strict 42% of default tokens, memory share 0.14≥0.02) → decision **`needs_forward_window`**. Baseline stamped. Evidence: `docs/eval/2026-09-18-prove-recall.json`.

---

- [x] Task 1: Strict recall policy + tests
- [x] Task 2: Prove-recall runner + decision tree tests
- [x] Task 3: CLI `prove-recall` + docs/eval README
- [x] Task 4: Live run + evidence JSON + stamp
