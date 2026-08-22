# Where the next milestone comes from

Written after a day in which three separate estimates came in at a third to a
tenth of their first guess. Everything here is measured; where it is not, it
says so.

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

## What is actually reachable

Running the real compressors bucket by bucket rather than assuming a rate:

| | tokens | of context | basis |
|---|---|---|---|
| **C.** what memor removes today | 510,024 | 6.88% | measured |
| **A.** exact duplicate payloads → pointer | 131,890 | 1.78% | measured |
| **B.** drop the 2KB floor | 45,491 | 0.61% | measured |
| **D.** fetched pages | 79,818 | 1.08% | measured |
| **combined** | **767,223** | **10.35%** | |

So the honest next milestone is **7% → ~10%** of total context, not 46%.

### On (D), and why the number moved

I first estimated fetched pages at 40%, which would have been 5.58% of context
and the largest single win available. Then I implemented a conservative squeeze
— collapse blank runs, drop short lines that repeat four or more times in one
page (nav, footers, chrome) — and ran it over all 825 payloads:

```
before  1,083,786
after   1,003,968
saved      79,818   (7.4%)
```

7.4%, not 40%. Fetched pages arriving through `webfetch` are already rendered
to text by the fetcher, so the boilerplate a real HTML extractor would strip is
mostly gone before memor sees it. The same mistake as the earlier `html` and
`csv` claims: pattern presence is not compressibility.

## The source slice is not as untouchable as the table says

The 17.0% "source in tool results" row above is not one thing:

| | tokens | of context |
|---|---|---|
| superseded file reads (older copy of a re-read file) | 420,701 | 5.67% |
| ...skeletonizing them saves | 28,724 | 0.39% |
| source-looking payloads with **no** `file_path` | 1,118,513 | 15.08% |

The skeletonizer only fires when the payload can be tied to a file, and 770,784
of those unattributed tokens come from `bash`, not from a file read. Inspecting
the 524 blocked bash payloads:

```
look like an actual code dump: 272,205 tokens
look like command output:      280,570 tokens
   e.g. '# Review package: 663b7f62..HEAD'
        'commit 22377770b85322ea019554728b9d4e5cb6a0ba29'
        'diff --git a/apps/backend/...'
```

Roughly half is `git log`, `git diff` and review reports that the guard
classifies as source because they *contain* code. That is the guard doing its
job conservatively, but it is also ~280K tokens (3.8% of context) held back by
a classification, not by a real risk of corrupting an edit. A diff-aware
compressor is the specific unlock, which is the same conclusion
`compression-gap-analysis.md` reached from the other direction.

This does not change the 10.35% figure, because none of it is implemented. It
does mean the 46% "untouchable" split is softer than stated: part of the 17%
is reachable with a better classifier rather than with more risk.

## Ranking by value per unit of risk

1. **Exact duplicate elision — 1.78%, and the safest thing on this list.**
   Byte-identical payloads already sent. Replacing a repeat with a pointer to
   the first copy cannot lose information, because the content is verbatim
   earlier in the same context. CCR already stores originals, so the machinery
   exists. This is the clearest remaining win.

2. **Fetched-page squeeze — 1.08%.** Lossless-ish and self-contained, but small,
   and dropping repeated short lines needs the same answer-critical retention
   probe used on the log compressor before it ships.

3. **Lower the 2KB floor — 0.61%.** One constant. The floor was a guess, and the
   measurement says the content beneath it is genuinely thin, so this is a
   cheap tidy rather than a milestone.

4. **Diff compressor — ~16% of diff payloads (~145K tokens).** Measured
   separately in `compression-gap-analysis.md`. Overlaps bucket B/D partly.

## What is not worth doing

- **Session-start compression** — 4.5%, and it needs resume frequency measured
  first (`session-start-analysis.md`).
- **Output shaping** — fires on 1.3% of turns, and output is 0.2% of this
  machine's bill. Seven months to measure.
- **Tabular / HTML compressors** — retracted, no workload.

## The honest summary

The product is not near a limit: it captures 15% of what it is permitted to
touch. But the remaining work is several 1-2% wins rather than one large one,
and the largest untouchable slice (31.3%, the code being written) will stay
untouchable for as long as correctness matters more than tokens.
