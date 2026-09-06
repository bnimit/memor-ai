# Graft: what it does differently, and what memor should take from it

Read 2026-09-06 from <https://github.com/trailhq/Graft> (5.6k stars, 521 forks,
464 commits, MIT, created 2026-07-03, last push 2026-09-05 -- so two months old
and actively developed). Positioned the same way memor is: one context layer
across Claude Code, Cursor, Codex, Gemini and others. It is the closest thing to
a direct comparison that has published task-outcome numbers, which is why it is
worth a document of its own rather than a row in the landscape table.

Nothing here is a recommendation to copy Graft. The two projects answer
different questions, and the most useful finding is that they barely overlap.

## The one-line difference

**memor remembers what you did. Graft explains what the code is.**

memor's store holds 32,650 `session_chunk` and 2,750 `memory` artifacts, and its
only edge types are `derived_from` (7,311) and `supersedes` (5,960) -- a
provenance graph over conversations. It contains no structural artifact at all:
no symbol, no call edge, no file relationship.

Graft is the reverse. It parses the repo with tree-sitter into a per-symbol
wiring graph plus LLM-written concept nodes, and holds no conversation history
whatsoever. Close a Graft session and everything you worked out is gone, which
is precisely the loss memor exists to prevent.

| | memor | Graft |
|---|---|---|
| Substrate | conversation transcripts | source code |
| Storage | SQLite + sqlite-vec + FTS5 | markdown files + JSON, gitignored |
| Retrieval | embeddings + BM25 + MMR | grep and link-following, no embeddings |
| Freshness | daemon polls session stores | rebuilds against working tree per query (~3ms) |
| Survives a session? | yes, that is the point | no |
| Survives a refactor? | degrades: memories reference moved code | yes, rebuilt from the tree |

These are complements. A team could run both, and neither would notice the
other.

## Four things worth taking

### 1. A task-outcome benchmark, not just self-measurement

This is the significant one, and it is a genuine gap.

memor measures itself thoroughly and honestly: realized savings from the ledger,
episode-level recall accounting, cache-aware net figures, and a `no_effect`
verdict on 3,309 episodes that it publishes rather than buries. But every one of
those numbers is memor observing its own traffic. None of them answers *did the
agent do the job better*.

Graft ran SWE-bench Verified, 50 instances, same model both arms, official
grader. Not a judge model and not a similarity score: the patch is applied and
the maintainers' own tests decide. It reports 27/50 to 33/50.

That is a claim memor currently cannot make in either direction. `memor/eval/`
holds `answer_retention.py`, `longmemeval.py`, `counterfactual.py` and
`judge.py` -- retrieval-quality and retention harnesses -- but nothing that
runs a real task to a pass/fail outcome. The honest position today is "we do not
know whether memor makes agents better at their work", and the CONFOUND_NOTE
already says as much for the observational arm.

Worth noting SWE-bench is a poor fit for memor as-is: its instances are
single-shot, one repo, no prior session history, so a memory layer has nothing
to remember. A fair harness would need repeated related tasks in one repo, where
session two can benefit from session one. That is a build, not an adoption.

### 2. Convergent evidence on cache-aware pricing

Graft's benchmark prices cache reads at 0.1x and writes at 1.25x. memor's
`compression_worth.py` uses exactly the same two constants, for exactly the
stated reason -- a write multiplier above 1.0 is the only way the arithmetic can
show compression *losing* money.

Two independent projects landing on the same model is the strongest available
corroboration that memor's net-of-cache accounting is built on the right basis.
This is a confirmation, not a lesson.

### 3. Freshness as a design property, not a maintenance task

Graft rebuilds structurally before answering, costs nothing, takes ~3ms when
nothing moved, and never calls a model to do it. There is no stale index to
babysit.

memor's equivalent is a polling daemon, and its artifacts have no freshness
concept at all: a memory recorded in August about a file refactored in September
is still returned at full confidence. Nothing in the store records that the code
it describes has moved. This is a real weakness and it is *not* fixed by
adopting Graft's mechanism, because conversation memories cannot be regenerated
from the working tree.

The obvious narrower fix -- check a memory's cited file against that path's
current content hash -- does not apply either, and I checked before claiming it:
all 2,750 memories carry `source` of `distill` or `promotion`, and **none
records a file path**. There is nothing to hash against. Making memories
staleness-aware would first require the distiller to record which files a
memory is about, which is a feature, not a tweak. Worth knowing the cost before
it is proposed as a quick win.

### 4. Zero-infrastructure defaults

`graft build` needs no key, no network, and no model: pure tree-sitter. The LLM
layer is opt-in via `--deep`. Cost and privacy objections are answered by
default rather than by configuration.

memor is already local-first, but it requires a daemon, a proxy and a dashboard
service. That is three background processes against Graft's zero. The `graft/`
directory being a gitignored regenerable cache, with only the small wiring
committed, is a cleaner story than a SQLite database that is the only copy.

## What memor has that Graft does not

Stated plainly, because the comparison should not read as one-sided:

- **Cross-tool memory transfer.** Work done in Codex is recallable in Claude.
  Graft's graph is shared across agents, but it is derived from the code, so
  every agent would have built the same thing independently. memor moves
  something that genuinely only existed in one place.
- **Decisions and their reasoning.** Why a design was rejected is not in the
  source and never will be.
- **Honest negative reporting.** memor publishes `no_effect` and labels its own
  headline an upper bound. Graft's README reports wins.
- **Measurement of the deployed system on the user's own traffic**, rather than
  on a benchmark corpus.

## The uncomfortable read

Graft's central claim is that agents waste most of a run rediscovering a
codebase they mapped an hour ago. `docs/competitive-landscape-2026-08.md`
already identified re-explaining the codebase as one of three binding
constraints, and named memor's memory half as the piece attacking it.

Graft attacks the same constraint from the other side, needs no history to work
on day one, and has SWE-bench numbers. memor's answer -- that decisions and
reasoning live only in conversation -- is correct and is the stronger long-term
position, but it is currently unmeasured against task outcomes while Graft's is
not.

The gap to close is evidential, not architectural.
