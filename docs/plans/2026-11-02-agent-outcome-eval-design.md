# Design: an eval that runs the agent and checks the work

Status: **design, not built.** Written 2026-11-02. Supersedes nothing in
`2026-09-06-task-outcome-benchmark-design.md`; it extends it, corrects one
claim in it, and adds the tier that can ship first.

## Why this is being picked up again

Two things changed since September.

**The blocker is gone.** That document stopped at
`"Failed to authenticate: OAuth session expired"`. On 2026-11-02, `claude -p
"..." --output-format json` runs headless here and returns `total_cost_usd`,
`num_turns`, and a `usage` block that splits cache writes from cache reads.
No new instrumentation is needed to measure either arm.

**The reason to want it changed.** In September the gap was evidential: memor
could not say whether an agent does better work with it. It is now also
commercial. Over 14 days to 2026-11-01 the repository had 18 unique visitors,
13 of them from a single referrer on one day, and no new stars. Nobody has
rejected memor; almost nobody has seen it. What a project with no audience
needs is one checkable claim on the front page, and memor cannot make one
today.

## The correction: single-shot tasks do measure compression

The September document rules out SWE-bench, correctly, for memory:

> SWE-bench instances are **single-shot**: one repo, one issue, no prior
> session history. A memory layer has nothing to remember on the first and
> only task, so memor would score identically to baseline by construction.

That argument is sound for recall and does not apply to compression. The
compression hook rewrites tool output *within* one session: a 5,000-line test
run, a grep across a package, a stack trace repeated on every retry. A
single-shot SWE-bench instance is full of exactly that, and it is resent on
every turn for the rest of the episode. Memory needs a task pair; compression
needs only a long task.

So the work splits in two, and the halves have very different costs and very
different odds of a positive result.

| | Tier 1 — compression | Tier 2 — memory |
|---|---|---|
| Question | Does the agent do the same work for less money? | Does the agent do better work with memory? |
| Unit | one task | a task *pair* (session A, then a fresh session B) |
| Tasks | public, graded by the repo's own tests | has to be built; no public benchmark fits |
| Prior evidence | strong on tokens, unmeasured on outcomes | `no_effect` on 3,309 episodes; holdout running |
| Expected result | a real cost reduction, size unknown | plausibly null |
| Build | ~2 days | ~1-2 weeks |

Tier 1 first. It is the headline, it is cheap, and its result is informative
either way: if the agent scores the same for meaningfully less money, that is
the claim; if compression makes the agent fail more, that is a bug worth more
than any claim.

## Tier 1 in detail

### The two arms

Identical in every respect but one. Same model, same repository snapshot, same
prompt, same tool set, same permission mode, same turn limit.

```
treatment: claude -p "<task>" --setting-sources "" --settings arms/memor.json
control:   claude -p "<task>" --setting-sources "" --settings arms/bare.json
```

`--setting-sources ""` detaches the run from whatever is in `~/.claude` and
the repository, so the arm file is the entire difference and a developer
machine's own hook settings cannot leak into either arm. `arms/memor.json` carries
the `PostToolUse` compression hook; `arms/bare.json` is the same file with the
hook removed. Both arms must be checked once with `memor doctor`-style
assertions that the hook actually fired (treatment) and never fired (control),
because the failure mode this project already lived through — a matcher that
silently compiled to nothing for seven weeks — would show up here as a
flawless null result.

The proxy path is out of scope for tier 1. It rewrites conversation history,
which interacts with the provider's cache in ways that need their own
experiment; the hook rewrites a payload once, before it is ever sent.

### Tasks

Requirements, in order:

1. **An automatic pass/fail.** The repository's own tests decide. No judge.
   `longmemeval.py` already records why, in this project's own history: a
   counterfactual win rate moved from 63.8% to 8.6% when the harness was
   corrected to call production `recall()`. A judge measures the judge.
2. **Long enough to have something to compress.** A task the agent finishes in
   three turns cannot show a difference. Filter candidates by a dry run: keep
   instances where the control arm produces at least ~50K tokens of tool
   output.
3. **Deterministic to grade.** The test command and its expected result are
   fixed before any arm runs.

SWE-bench Verified is the obvious source: 500 human-validated instances, each
with a repository snapshot, an issue, and `FAIL_TO_PASS` / `PASS_TO_PASS` test
lists. Its grading is standard and free, and using it makes the number
comparable with other published work. Its cost is setup: per-instance Docker
images and a large download.

The fallback, if that setup proves heavy, is 8-10 hand-built tasks across two
or three real repositories with fast suites. Weaker, because the task set is
ours, so it must then be published in full alongside the result.

### What is measured

Primary, and the only two that go in a headline:

- **Pass rate**, by the instance's own tests.
- **Billed cost**, from `total_cost_usd` as the CLI reports it.

Cost is reported in dollars, never in a token count. A measured example of why:
a `claude -p` run that replies with the single word "ok" cost **$0.1228** here,
of which $0.1096 was the one-hour cache write for the system prompt and tools.
Tokens and dollars are not proportional, and a saved token that was being
served from cache is worth about a tenth of a fresh one — the figure the
dashboard now shows as the lower bound. The LinkedIn post that prompted this
work reported 33× fewer tokens and 7.5× cheaper in the same table; the gap
between those two numbers is the whole reason to quote the second one.

Secondary: turns, wall clock, tokens by class (fresh input, cache read, cache
write, output), hook firings and bytes rewritten, and — where the task fails —
whether it failed for a reason the compression could plausibly have caused.

### Repetitions and what the result can honestly say

Agent runs are stochastic: the same task and the same arm do not always reach
the same verdict. The design is paired — every task runs in both arms — and
each (task, arm) runs **3 times**.

With 20 tasks × 2 arms × 3 repetitions = 120 runs:

- **Cost** is a paired comparison with low variance relative to a large
  effect. If compression cuts billed cost by 10% or more, 20 paired tasks will
  show it clearly.
- **Pass rate** is the weak one, and this must be stated in the result rather
  than discovered by a reader. Sixty runs per arm can rule out a large
  regression, not a small one. The honest phrasing is "no regression detected
  (57/60 vs 56/60, difference −1.7pp, 95% CI −9 to +6pp)", never "same score".
  Claiming equivalence from a small sample is the single easiest way to lose
  the credibility this eval exists to build.

A pre-registration file — tasks, arms, model, repetitions, and the exact
metrics — is written and committed *before* the first run, so the analysis
cannot drift toward the answer we want.

### Cost and time, estimated from a measurement

Fixed overhead is $0.12 per run before the agent does anything. A real
SWE-bench-shaped task is longer: on Claude Sonnet 5.5 ($2/$10 per MTok),
roughly $0.40-$1.50 per run; on Claude Opus 5.5 ($4/$20), roughly 2.5× that.

| Configuration | Runs | Estimate |
|---|---|---|
| Pilot: 5 tasks × 2 arms × 2 reps, Sonnet 5.5 | 20 | $10-30 |
| Headline: 20 tasks × 2 arms × 3 reps, Sonnet 5.5 | 120 | $50-180 |
| Confirmation: 8 tasks × 2 arms × 2 reps, Opus 5.5 | 32 | $40-120 |

Serial wall clock is hours; 4-8 concurrent runs bring the headline set to
roughly 1-3 hours. The pilot runs first and its real numbers replace these
estimates before the headline set is approved.

### Where it lives

```
memor/eval/agent_outcome.py     harness: run one (task, arm), collect the JSON
memor/eval/tasks/               task definitions + the pre-registration file
memor/eval/arms/                memor.json, bare.json
docs/eval/YYYY-MM-DD-agent-outcome.json   results, committed
```

Run on demand like `hook_worth.py`, never in CI: it costs money and needs
network, and a release gate that spends dollars is a release gate people
disable.

## Tier 2, unchanged in shape

The September design holds: the unit is a task pair, session A does work that
memor ingests, a fresh session B attempts a related task, and B's *outcome* is
scored rather than B's retrieval. Its three pair shapes — decision then
application, debug then recurrence, exploration then extension — still look
right, and the first is still the strongest.

One addition. `memor recall-holdout` now exists, so tier 2 has a second route
that costs nothing: leave the holdout running on real traffic and let
`memor recall-worth` compare arms on the user's own work. It is slower (weeks)
and it answers a narrower question (cost, not correctness), but it is free and
it is already running. Build the paired harness only if the holdout stays
ambiguous or shows an effect worth confirming.

## What may be published, and when

- After tier 1 passes: a cost claim with its measured pass-rate interval
  beside it, the task list, and the raw results JSON in the repository.
- After tier 1 only: **no claim about memory**, and no claim that the agent
  does better work — only that it does the same work for less.
- Tier 2, and only tier 2, licenses a claim about memory. Until then the
  README keeps saying what it says now: memor measures what it costs and what
  it retrieves, and has not yet measured whether an agent does better work
  with it.

If tier 1 shows compression costing more than it saves on real tasks — which
cache behaviour makes possible — that result gets published too, and the
default changes.
