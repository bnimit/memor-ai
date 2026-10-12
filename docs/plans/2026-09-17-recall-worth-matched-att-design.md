# Recall-worth matched ATT — design

**Date:** 2026-09-17  
**Status:** Approved for implementation (Approach 1′)  
**PR:** #47 (`fix/savings-hero-lifetime-and-hook-dedupe`)  
**Related:** `memor/episodes.py`, `memor recall-worth`, dashboard `/api/recall-worth`

## Problem

`memor recall-worth` already meters Claude Code episodes and is correctly
conservative: it returns `no_effect` when prompt-length strata disagree on sign.
On the development machine that veto fires in practice (mid bands look helpful;
`400-+` looks harmful). The headline is therefore unusable for marketing even
though the plumbing works.

Composition confounds remain:

- Recalled vs control arms are not the same project mix (partially addressed by
  `project_adjusted_delta`).
- Longer prompts get recall more often and are different work.
- `had_recall` means “something was injected,” not “a useful memory helped.”
- The comparison is observational (recall fires on good matches / familiar work).

## Goal

Produce a **single credible Claude Code verdict** (`saves` | `costs` |
`no_effect` | `insufficient_data`) driven by a **matched average treatment
effect on the treated (ATT)**, so we can market memory ROI only when the meter
supports it — and ship a confident null without embarrassment.

## Approach (1′)

Matched ATT within **project × prompt-length band**, with light calipers and a
written verdict rule that supersedes raw stratum sign-flip as the headline.
Strata remain diagnostics. Holdout randomization is a future experiment, not
this PR.

## Scope

**In (this PR series):**

1. Treatment hygiene for what counts as recalled.
2. Matched ATT (project × prompt band + calipers) with match-rate reporting.
3. Pre-registered `400-+` policy.
4. Verdict / CLI / dashboard contract: one headline number.
5. Decision tree after re-run on the local Claude corpus → M2 only if powered
   null with good match rate.

**Out (deferred, not abandoned):**

| Item | Later when |
|---|---|
| Multi-agent episode parsers | Claude meter is credible |
| `MEMOR_RECALL_HOLDOUT` | Matched ATT still selection-bound / ambiguous |
| LoCoMo / Mem0 bake-off | Optional marketing comparison |
| Flip `MEMOR_SUPERSESSION` defaults | Stratified counterfactual clears (existing M1b path) |
| Market before a non-null `saves` | Never — deliberate non-goal |

Claim language until multi-agent lands: **Claude Code episode ROI**, not “memor
in general.”

## Components

### A — Treatment hygiene

An episode is **treated** only if:

- `had_recall` is true, **and**
- `recall_chars >= MIN_RECALL_CHARS` (default **80**, ~one short memory block;
  configurable in code constant, documented in report), **and**
- the episode is usable (`assistant_steps > 0`).

Episodes with a recall marker but below the char floor are **excluded from both
arms** (neither treated nor control), so weak injects do not dilute ATT or
contaminate controls.

### B — Matching

For each treated episode \(T\), find a control \(C\) such that:

1. Same `project`
2. Same prompt-length stratum (existing `_PROMPT_STRATA` bands)
3. **Caliper:** `|log1p(C.prompt_chars) - log1p(T.prompt_chars)|` minimized;
   reject if prompt_chars ratio outside \([1/3,\ 3]\) (too unlike)
4. **Optional second key (when available):** same session-phase bucket from
   ordinal position in conversation (`early` = first 20% of episodes in that
   `conversation_key`, `mid`, `late`). If conversation has &lt; 5 episodes, skip
   phase constraint.

Matching rules:

- Prefer unused controls (without-replacement); if none left in cell, allow
  with-replacement but flag `reuse_rate`.
- Unmatched treated episodes are dropped from ATT and counted in
  `unmatched_treated`.
- Report `match_rate = matched / treated_eligible`.

Primary effect:

\[
\widehat{\mathrm{ATT}} = \mathrm{mean}_i\bigl(Y_i(0) - Y_i(1)\bigr)
\]

in **tool-call percent terms** consistent with today: positive = recall did
**less** tool work than its match (same sign convention as
`tool_call_delta_pct`).

Also report matched mean token delta as secondary (diagnostic only; not in
verdict).

### C — `400-+` policy (pre-registered)

- Band `400-+` is labeled **exploratory** in the report.
- It is **included in matching and ATT**.
- It does **not** veto the headline via stratum sign-flip.
- Diagnostic strata still print its delta; copy states it is non-decisive.

Rationale: long pasted prompts are a different task class; letting them veto
mid-band effects was the false “no signal” reading.

### D — Verdict rule (headline)

Let:

- `att` = matched tool-call delta %
- `mde` = MDE % on the matched pair differences (same 2.8·SE spirit as `_mde_pct`)
- `match_rate` = fraction of eligible treated episodes matched
- `MIN_MATCH_RATE` = 0.60
- `MIN_PAIRS` = 50
- `EFFECT_THRESHOLD_PCT` = 10.0 (existing)

```
if pairs < MIN_PAIRS or match_rate < MIN_MATCH_RATE:
    → insufficient_data
elif abs(att) <= max(EFFECT_THRESHOLD_PCT, mde):
    → no_effect          # powered or unpowered null; see decision tree
elif att > 0:
    → saves
else:
    → costs
```

**Stratum sign-flip no longer sets the headline.** Project-consistency and
strata remain in the JSON/report as diagnostics. `project_adjusted_delta` stays
as a secondary block for continuity.

Update `VERDICT_TEXT` / `format_report` so `no_effect` copy mentions matched ATT
and match rate, not “sign flips across bands,” when the new path is active.

### E — Decision tree (post-run, same PR series)

After implementing A–D, run `memor recall-worth` on the local Claude corpus:

| Result | Action |
|---|---|
| `saves` or `costs` with match_rate ≥ 0.60 and pairs ≥ 50 | Stop meter work; do **not** start M2 on this PR. Document evidence JSON under `docs/eval/`. |
| `insufficient_data` (low match rate / few pairs) | Improve matching/calipers or floors — **not** M2. |
| `no_effect` with match_rate ≥ 0.60, pairs ≥ 50, and `abs(att) < mde` or below threshold | **Powered null** → start **M2 memory-vs-chunk** as the next commit series on the same PR. |
| `no_effect` but match_rate &lt; 0.60 | Treat as insufficient matching, not product failure. |

### F — Surfaces

- CLI `memor recall-worth`: print matched ATT, match rate, pairs, verdict first;
  strata below.
- `/api/recall-worth` + dashboard panel: headline uses matched fields
  (`matched_att_pct`, `match_rate`, `verdict`).
- Evidence file: `docs/eval/YYYY-MM-DD-recall-worth-matched.json` after the
  local re-run (flags/decision for M2 gate).

## Data flow

```
Claude transcripts → scan_episodes / parse_episodes
        → treatment hygiene (exclude weak injects)
        → eligible treated + controls
        → match (project × band × calipers)
        → matched ATT + MDE + match_rate
        → verdict (rule D)
        → CLI / API / dashboard
        → decision tree E → maybe M2
```

## Error handling / honesty

- Always emit `confound` note (observational upper bound).
- Always emit `claim_scope: "claude_code_episodes"`.
- Never imply causality from matched ATT alone.
- Holdout path documented as future, not stubbed as a fake flag.

## Testing

- Unit: matching prefers same project/band; rejects ratio outliers; without-
  replacement; match_rate math; weak injects excluded from both arms.
- Unit: verdict table (insufficient / null / saves / costs) with fixtures.
- Unit: `400-+` diagnostic present but cannot alone force `no_effect` when
  matched ATT is significant the other way.
- Regression: existing episode parse / turn-boundary tests stay green.
- Manual: run `memor recall-worth` on `~/.memor` + Claude projects; write
  evidence JSON; apply decision tree.

## Non-goals (reminder)

Randomized recall holdout, multi-agent parsers, LoCoMo, supersession default
flip, marketing a `saves` claim before the meter returns one.
