# Where the next milestone comes from

Written after a day in which three separate estimates came in at a third to a
tenth of their first guess. Everything here is measured; where it is not, it
says so.

**Revised** after measuring against the 2,465-session Claude corpus
(82.5M tokens) rather than the 84-session local one. Three of the four
recommendations below did not survive it. The corrections are kept in place
rather than deleted, because the pattern that produced them keeps recurring.

## Where "46%" came from, in plain terms

Every token in a jcode request, 7,414,755 across 84 local sessions:

| slice | tokens | share | can we touch it? |
|---|---|---|---|
| tool_use args | 2,323,399 | 31.3% | **No** — the code the agent is writing |
| source in tool results | 1,260,233 | 17.0% | **No** — the agent edits against these |
| conversation text | 420,194 | 5.7% | **No** — rewriting re-forms the cached prefix |
| tool results under 2KB | 1,337,570 | 18.0% | yes — below our own floor |
| large results, no gain today | 1,108,748 | 15.0% | yes — mostly fetched web pages |
| large results, compressible | 964,611 | 13.0% | yes — already handled |

The three "yes" rows are 46%. That is **permission, not opportunity**: it is
what we are allowed to rewrite, assuming every touchable token vanished
entirely. Nothing compresses to zero, so 46% is not a target and never was. It
only says the work is not finished.

## The larger corpus

82,543,693 tokens over 2,465 sessions:

| slice | tokens | share |
|---|---|---|
| tool_result | 50,091,394 | 60.7% |
| tool_use args | 19,554,337 | 23.7% |
| everything else | 12,897,962 | 15.6% |

Running the real compressors over a 400-session sample (7.9M tool-result
tokens), bucket by bucket:

| bucket | payloads | tokens | saved | rate | share |
|---|---|---|---|---|---|
| source (refused) | 1,739 | 3,861,944 | 0 | — | 48.8% |
| under the 2KB floor | 10,421 | 1,432,204 | 0 | — | 18.1% |
| text | 878 | 1,172,858 | 16,643 | 1.4% | 14.8% |
| **log** | 451 | 1,025,469 | **719,422** | **70.2%** | 13.0% |
| search | 197 | 232,769 | 80,069 | 34.4% | 2.9% |
| diff | 110 | 178,589 | 118 | 0.1% | 2.3% |
| json | 6 | 7,302 | 4,948 | 67.8% | 0.1% |

**10.38% of tool-result tokens, and 88% of it is the log crusher.** Everything
else combined is 1.2%. That single fact should drive the next decision more
than any of the rankings below.

## Corrections to the ranking

### Exact duplicate elision: 1.78% → 0.53%, and it has no mechanism

Measured on the large corpus: 11,409 exactly-repeated payloads, 441,187
recoverable tokens, **0.53% of total context** — and only 0.09% if the 2KB
floor is respected. The repeats are overwhelmingly small payloads.

Worse, the stated justification was wrong. "CCR already stores originals, so
the machinery exists" is true of the **proxy** path only: `ccr_put` is called
in exactly one place, `memor/proxy/pipeline.py:202`. The hook path never
stores an original, never emits a `[memor:ccr:...]` marker, and is stateless
per payload — it cannot even tell that it has seen a payload before. And the
hook is the path that matters now: `ccr_blobs` currently holds **0 rows**.

So this is not "the safest thing on the list, machinery already exists". It is
0.53% *plus* building cross-payload state in the hook. It drops to last place.

### Superseded file reads: 15.77% naive → 0.79% safe

The largest number measured all session, and almost all of it is an artifact.

When one file is read twice, the older copy looks redundant: 24.7% of all
Read-tool tokens, 15.77% of tool-result tokens. It is not one pathological
file either (365 distinct files, the top 8 only 18% of the mass), which is
usually the sign of a real effect.

But `Read` takes `offset`/`limit`, and **96% of those "stale" tokens are a
different region of the same file**, not an older copy of the same region.
Eliding them deletes content the newer read never contained — exactly the
corruption the source guard exists to prevent, reintroduced from a direction
the guard does not watch.

Safe subset: the newest read used the same arguments, or is a whole-file read
that demonstrably covers the earlier one. That is 62,701 tokens, **0.79%**.

### A better classifier is worth 0.03%–4.04%, and the range is the finding

Of the 3,861,944 tokens the source guard refuses, 87.7% are genuinely
line-numbered file reads that must stay protected. A perfect classifier could
release the other 12.3% (476,129 tokens).

What that is worth depends entirely on which compressor receives them:

```
released to the log crusher : 319,248 saved (67.1%)  = 4.04% of tool-result tokens
released to plain text      :   2,719 saved ( 0.6%)  = 0.03%
```

**The classifier is not the bottleneck; the receiving compressor is.** This is
the third time today the same shape appeared: releasing a payload grants
permission to compress it, and the compressor then declines on merit. The
fetched-document work shipped and returned 0.10% against a projected 5.39% for
precisely this reason.

So "build a better classifier" is the wrong next step. The right question is
which released payloads are *log-like*, because that is where the 67% lives.

### The embedding model is not this classifier

Worth stating because the names collide. `model2vec` / `potion-base-8M`
(`memor/embed/local.py`) is the **retrieval** embedder: it scores memories for
recall. The compression classifier is `detect_content_type`
(`memor/compress/detect.py`), which is pure regex and structure heuristics.
Nothing in `memor/compress/` imports an embedder.

Swapping the embedding model would not change compression at all. And an
embedding model is a poor fit for this job regardless: the decision is
"is this payload a file the agent will edit against", which turns on
provenance and structure (line-number prefixes, fence ratios, `file_path`),
not on semantic similarity. The signals that work are cheap and exact; the
failures today were logic-ordering bugs, not weak features.

## Revised ranking

1. **Widen what reaches the log crusher.** It is 88% of realised savings at a
   70.2% rate, and 4.04% more sits in source-classified payloads that are
   log-like. Everything else on this list is under 1%.
2. **Lower the 2KB floor — 0.61%.** 18.1% of tool-result tokens are below it
   and currently untouched. One constant, already measured.
3. **Superseded file reads — 0.79%**, with the region check above. Needs the
   same integrity probe the log crusher has.
4. **Fetched-page squeeze — 1.08% on the local corpus**, unverified on the
   large one.
5. **Exact duplicate elision — 0.53%**, and it needs hook-side state first.

## What is not worth doing

- **Session-start compression** — 4.5%, needs resume frequency measured first
  (`session-start-analysis.md`).
- **Output shaping** — fires on 1.3% of turns; ~211 days to conclude.
- **Tabular / HTML compressors** — retracted, no workload.
- **A smarter classifier as such** — see above; the constraint is downstream.
- **Diff compressor improvements** — shipped at 0.1%. `git diff` emits 3 lines
  of context per side, so runs are too short to elide. Its value was the
  reclassification, not its own savings.

## The honest summary

The product captures roughly 10% of tool-result tokens, and one compressor
does nearly all of it. The remaining ideas are 0.5–1% each, several are
smaller than first measured, and two of them (duplicates, superseded reads)
carry correctness hazards that only appear when you look at the tool arguments
rather than the payload text.

The recurring error this session has a single shape: **counting mass that
matches a pattern, instead of what a compressor actually removes from it.**
Six estimates collapsed that way. The seventh, superseded reads, collapsed for
a related reason — counting payloads that *look* redundant without checking
whether they hold the same bytes.
