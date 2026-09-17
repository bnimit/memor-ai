# Temporal validity / supersession eval

Evidence for M1b flag flips lives in this directory as dated JSON files
(`YYYY-MM-DD-temporal-validity-m1b.json`). Do **not** default
`MEMOR_SUPERSESSION` or `MEMOR_TYPE_HALFLIFE` to on without a green evidence
file that clears the ship bar in
`docs/plans/2026-09-17-temporal-validity-and-recall-design.md`.

## Hard gates (CI)

```bash
pytest tests/test_supersession_fixtures.py tests/test_similarity_cosine.py \
  tests/test_supersession_disputes.py tests/test_retriever_supersession.py \
  tests/test_type_halflife.py tests/test_counterfactual_stratum.py -q
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
