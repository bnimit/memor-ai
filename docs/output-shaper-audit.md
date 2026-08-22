# Auditing the output shaper

What it would take to know whether output shaping helps, and why the current
design cannot deliver that answer on this machine's traffic.

## What has already been checked

A **static replay** over 11,187 real assistant turns, asking only "would the
gates have fired here, and did that turn go on to write code". That found two
real defects (case-sensitive write-tool matching, and bare approvals of a
proposed change) and is committed as `2c82bb0`.

What it does **not** answer: whether shaping a turn the gates allow degrades
the result. A replay cannot answer that, because the model never ran under the
instruction. Only a live A/B can.

## What a real audit needs

Three things, none of which exist yet:

1. **A decision ledger.** `proxy_savings` has no column for shaped/holdout, and
   there is no `output_shaping` table. Every request needs its arm recorded:
   `shaped`, `holdout`, or the decline reason.
2. **A control arm.** `MEMOR_OUTPUT_HOLDOUT` already assigns conversations
   stably (`in_holdout`), so this is wiring, not design.
3. **Quality metrics, not just token counts.** Fewer output tokens is the
   *intended* effect, so measuring only that cannot detect harm. The signals
   that would show harm:
   - **retry rate** — user re-asks the same thing within N turns
   - **edit size** — a terser model writing smaller diffs is the failure mode
   - **follow-up tool calls** — did the turn need more work to land
   - **user interruption** — the strongest signal, and already in the transcript

## Why it will not work here

Output tokens on this machine, 1,452 real requests with usage reported:

```
p50      9 tokens
p90      9 tokens
p95      9 tokens
p99  1,444 tokens
mean    48   median 9    -> extreme right skew
```

Nearly every turn emits almost nothing; a handful emit thousands. Sample size
to detect a reduction, two-sided, 80% power:

| effect | on raw means | on log scale |
|---|---|---|
| 10% | ~84,600 / arm | ~791 / arm |
| 20% | ~21,200 / arm | ~177 / arm |
| 30% | ~9,400 / arm | ~69 / arm |

Log scale is the right metric for skewed data and cuts the requirement by two
orders of magnitude. Even so:

```
usable requests    72/day
shaper fires on    1.3% of turns (140 of 11,187)
shaped turns/day   ~0.8  at a 90/10 split
time to n=177      ~211 days
```

**Seven months to detect a 20% effect.** The audit is not expensive to build;
it is impossible to conclude on one developer's traffic.

## What that implies

The gates are now so conservative that the thing cannot be measured, which is
also a statement about its value: 140 of 11,187 turns is 1.3% of turns, and
those turns are mostly short. The realistic saving is a rounding error on a
rounding error.

Three honest options:

- **Ship it dark.** Wire the ledger, default off, no claims. Costs little, and
  if the population using memor grows the data accumulates across users.
- **Widen the gates and re-audit.** More coverage means more risk to code,
  which is the trade the current design deliberately refuses.
- **Leave it.** The module is complete, tested, and documented. It costs
  nothing sitting unused, and the README makes no claim about output tokens.

The one thing not to do is turn it on and report an estimate. That is the
number Headroom reports (`31.7%, 95% CI 27.7–35.7%, [estimated]`), and matching
it would mean giving up the property that makes memor's numbers worth reading.
