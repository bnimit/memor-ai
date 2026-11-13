# Pilot: does the hook change what a coding agent costs?

Run 2026-11-13. Five tasks, two arms, two repetitions: 20 runs, all completed.
Raw per-run records are in `2026-11-13-pilot.jsonl`; the roll-up is in
`2026-11-13-pilot.summary.json`. The design this follows is
`../plans/2026-11-02-agent-outcome-eval-design.md`.

The pilot was meant to answer two questions before spending on the full run:
does the harness measure what it claims to, and is the effect big enough to see.
The answer to the first is yes. The answer to the second is no — and the reason
why is the useful part.

## What was run

Tasks are five of this repository's own fix commits, mined by
`memor/eval/mine_tasks.py`: check the commit out, put `memor/` back to the
parent, and the test the commit added fails while the rest of the suite passes.
The agent gets the commit's subject and body as a bug report and never the diff.

Both arms are `claude -p` on `claude-sonnet-5-5` with `--setting-sources ""`, the
same tool allowlist and the same `--permission-mode acceptEdits`. The only
difference is the settings file: the treatment arm registers memor's PostToolUse
hook, the control arm registers nothing.

Grading restores `tests/` from git before running pytest, so anything the agent
did to the tests is discarded.

## Results

Both arms resolved 10 of 10 runs: every FAIL_TO_PASS test passed and no
PASS_TO_PASS test broke, in both arms, on every task. No regression was detected
— but with both arms at the ceiling this task set cannot discriminate on
quality, so that is a sanity check, not a finding.

Cost, paired by task and averaged over repetitions:

| task | mean cost delta |
|---|---|
| memor-68a033c | −27.8% |
| memor-9f2a168 | −15.6% |
| memor-310d070 | −13.8% |
| memor-fd10ab9 | +5.7% |
| memor-eaa4790 | +12.2% |

Mean −7.9%, 95% CI −28.3% to +12.5% (t, 4 df). Totals: $1.5675 control,
$1.4540 treatment, $3.02 for the pilot.

**The $0.11 gap is not memor's.** Across the whole treatment arm the hook
removed 5,294 tokens — 0.13% of that arm's input tokens, worth about $0.02 at
Sonnet 5.5 list rates even if every one had been a cache write. The measured gap
is five times larger than the largest effect the hook could have produced, and
per-run savings and per-run cost deltas are unrelated: the run that saved 1 token
came in 20% cheaper, the run that saved 1,691 came in 4% dearer. Within one arm,
the same task re-run differs by up to 21%. The gap is the agent taking a
different path, not compression.

## Why the effect is absent

The hook only looks at tool output of at least 1,000 characters. Across ten
treatment runs, 15 outputs cleared that floor. The largest was 3,685 tokens.

Of those 15, the log and search compressors did their job in proportional terms
(2,542 → 600; 1,887 → 199; 1,146 → 734), but on so little volume that it does not
register against a $0.15 run. Seven firings were classified `text` and saved
between 1 and 3 tokens each — a 3,685-token `text` payload came back 3,684.
That is worth looking at on its own.

The task family is the problem. A targeted fix in a known file reads two or three
files, runs a focused pytest, and edits. It never produces the long logs, wide
greps and large file reads that memor exists to compress. This is the same
mistake the design doc warned about for recall and did not catch for compression:
the benchmark has to exercise the mechanism.

## What this means

Do not run the 120-run headline on these tasks. It would cost about $18 and
measure path variance to three decimal places.

Two things follow instead:

1. **The claim has to be scoped to long sessions.** On short targeted work the
   hook is close to a no-op, and any public number that implies otherwise would
   not survive someone rerunning it. If a savings claim is to be made, the tasks
   behind it must be ones that generate real tool-output volume — a full suite
   with verbose output, greps across a large tree, large file reads, long-running
   builds.
2. **The `text` compressor needs a look.** Seven of fifteen firings saved
   effectively nothing. Either large unstructured text is genuinely
   incompressible by the current method, in which case firing on it is wasted
   work, or the classifier is sending structured content down the wrong path.

The harness itself is sound and should be kept: 20 runs, clean arm separation
(15 hook firings in treatment, 0 in control), grading that resists test edits,
and cost read from the agent's own accounting rather than inferred.
