# Temporal validity / supersession eval

Evidence for M1b flag flips lives in this directory as dated JSON files
(`YYYY-MM-DD-temporal-validity-m1b.json`). Do **not** default
`MEMOR_SUPERSESSION` or `MEMOR_TYPE_HALFLIFE` to on without a green evidence
file that clears the ship bar in
`docs/plans/2026-09-17-temporal-validity-and-recall-design.md`.

## Prove-recall campaign

```bash
memor prove-recall                  # G0 matched ATT + G1 offline strict vs default
memor prove-recall --stamp          # also stamp forward window for G2
export MEMOR_RECALL_PROFILE=strict  # opt-in treatment in production
memor service restart
```

Evidence: `YYYY-MM-DD-prove-recall.json`. Ship bars in
`docs/plans/2026-09-18-prove-recall-design.md`.

- **G0** meter healthy (pairs/match_rate)
- **G1** strict ≤85% tokens vs default and ≥ memory share offline
- **G2** forward matched ATT after ≥7d under strict (required to market ROI)
- Do **not** default `MEMOR_RECALL_PROFILE=strict` until G1 passes and G2 is not `costs`

## Hard gates (CI)

```bash
pytest tests/test_supersession_fixtures.py tests/test_similarity_cosine.py \
  tests/test_supersession_disputes.py tests/test_retriever_supersession.py \
  tests/test_type_halflife.py tests/test_counterfactual_stratum.py \
  tests/test_recall_policy.py tests/test_prove_recall.py -q
```

Micro-fixtures must pass with `MEMOR_SUPERSESSION=1` (the test file sets it).

## Stratified counterfactual (release / nightly)

Needs a populated project DB and an LLM API key:

```bash
# Optional: populate disputes first
memor backfill-disputes --project <name>

# Baseline (flags off) vs treatment (flags on) — run twice and compare
memor eval-counterfactual --project <name>
MEMOR_SUPERSESSION=1 MEMOR_TYPE_HALFLIFE=1 memor eval-counterfactual --project <name>
```

The summary includes `by_stratum.dispute_present` and `by_stratum.no_dispute`.
Ship bar (design):

- Dispute-present do-no-harm improves vs baseline by more than the ±6pp noise floor
- No-dispute stratum does not regress
- Optional: sample dispute precision ≥ 0.80 (temp=0 judge on dispute edges)

Write the JSON evidence next to this README, then open a PR that flips defaults
only if both strata clear.

## Evidence JSON shape

```json
{
  "date": "YYYY-MM-DD",
  "commit": "<sha>",
  "flags_default": {"MEMOR_SUPERSESSION": false, "MEMOR_TYPE_HALFLIFE": false},
  "micro_fixtures": {"n": 15, "passed": true},
  "stratified_counterfactual": {
    "status": "not_run|passed|failed",
    "project": null,
    "baseline": null,
    "treatment": null,
    "notes": "..."
  },
  "decision": "keep_flags_off|flip_flags_on",
  "rationale": "..."
}
```
