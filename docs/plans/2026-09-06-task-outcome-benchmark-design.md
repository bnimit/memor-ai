# Design: a benchmark that can actually test a memory layer

Status: **design only, not built.** Written 2026-09-06 after the Graft
comparison (`docs/graft-comparison-2026-09.md`) identified this as memor's one
real evidential gap.

## The gap, stated precisely

Every harness in `memor/eval/` measures retrieval or compression quality:

| Harness | What it scores |
|---|---|
| `runner.py` | recall@k / nDCG@k against four baselines |
| `counterfactual.py` | an LLM judging whether recalled context *would have* helped |
| `answer_retention.py` | gold-answer containment after compression |
| `longmemeval.py` | retrieval on a public long-memory dataset |
| `hook_worth.py` | compression rate on real Bash output |
| `proxy_benchmark.py` | compression rate on fixtures |

Not one runs an agent at a task and checks whether it succeeded. `grep -rn
subprocess memor/eval/` returns nothing: no harness in this repo has ever
launched an agent.

`counterfactual.py` comes closest and is worth understanding, because its
limitation is the whole argument. It shows a judge the query, the recalled
context, and the *actual* continuation of the session, then asks whether the
context would have helped. The agent never runs. Nothing is attempted. A judge
predicting helpfulness is not evidence of task success, and the file's own
`do-no-harm rate` framing is appropriately modest about this.

## Why SWE-bench cannot close it

Graft ran SWE-bench Verified and reported 27/50 to 33/50. Adopting the same
benchmark is the obvious move and it is wrong.

SWE-bench instances are **single-shot**: one repo, one issue, no prior session
history. A memory layer has nothing to remember on the first and only task, so
memor would score identically to baseline by construction. Running it would
produce a null result that says nothing about memor, and quoting that null
either way would be dishonest.

Graft does not have this problem: its graph is built from source, so it is fully
useful on task one. That asymmetry is the deepest structural difference between
the two products, and it dictates a different benchmark rather than a shared one.

## What the benchmark has to look like

The unit of evaluation must be a **task pair**, not a task:

1. Session A performs some work in a repo. Memor ingests it as it would
   normally.
2. Session B, a *fresh* agent with no conversational context, attempts a
   related task in the same repo.
3. Arms: session B with memor recall wired in, and session B without.
4. Score B's outcome, not B's retrieval.

Memory can only pay off when B needs something that only A knows. That rules
out most public benchmarks and is the reason this has to be built.

### What makes a fair task pair

The relationship between A and B is the entire experiment, so it needs stating
rather than assuming. Candidate shapes, strongest first:

- **Decision then application.** A establishes a convention or rejects an
  approach with reasoning; B does related work where following that convention
  is checkable. This is memor's strongest claim -- the *why* genuinely exists
  nowhere but the transcript.
- **Debug then recurrence.** A root-causes a bug; B hits the same class of bug
  elsewhere. Checkable via time-to-fix and whether B re-derives the same cause.
- **Exploration then extension.** A maps a subsystem; B extends it. Weakest of
  the three, because this is the case Graft's structural graph serves better
  and memor should not claim it.

### Scoring

Correctness must come from something other than a judge. `longmemeval.py`
already documents why, from this project's own history: "on the same project
with the same config, the counterfactual win rate went 63.8% -> 8.6% when the
harness was corrected to call production `recall()`. A judge measures the judge
as much as the system." Options in order of preference:

1. **Repo tests.** B's task is chosen so the repo's own suite decides. Strongest
   and the SWE-bench standard.
2. **Assertions on the diff.** Did B touch the files the convention requires?
   Objective, weaker than tests.
3. **Judge with a keyword floor**, as Graft used. Last resort.

Secondary metrics come free from the existing ledger: tool calls, tokens, and
wall-clock per episode are already computed in `memor/episodes.py`.

## Feasibility, checked

`claude -p "..." --output-format json` runs headless and returns a
machine-readable result with `usage`, `num_turns` and `total_cost_usd` -- enough
for both arms without any new instrumentation. Verified the mechanism on this
machine (Claude Code 2.1.222).

**Blocker found while checking:** the CLI currently returns
`"Failed to authenticate: OAuth session expired and could not be refreshed"`.
The harness cannot be run here until that session is refreshed. This is a
credential state, not a design problem, but it is why this document exists
instead of a working harness.

## Cost, honestly

Each data point is two full agent runs. Meaningful n means dozens of pairs, so
this is real API spend, not a unit test. That argues for:

- a small curated set of pairs (10-20) rather than a broad sweep,
- pairs drawn from **this machine's own history**, where session A already
  exists in the store and does not need synthesising,
- running it on demand like `hook_worth.py`, never in CI.

## The honest position until this exists

memor measures what it costs and what it retrieves, thoroughly and with its
negative results published -- `no_effect` on 3,309 episodes is on the dashboard
rather than buried. What it does not measure is whether an agent does better
work with it than without.

That should be said plainly rather than implied away, and no marketing claim
about agent effectiveness should be made until this harness exists and has been
run.
