# The source guard: what it blocks, and what is safely reclassifiable

Follow-up to `next-milestone.md`. The guard holds 14.5% of context. This asks
how much of that is genuinely a file's contents and how much is a report that
merely quotes code.

## Measure removable, not present

Five estimates collapsed earlier in the day because they counted tokens matching
a pattern rather than tokens a compressor would actually remove. So everything
below runs the real compressors and counts the delta.

Blocked payloads at or above 2KB:

```
685 payloads, 1,072,217 tokens (14.5% of context)
removable if the guard vanished: 867,940 (80.9%)
```

80.9% removable is not an opportunity, it is the guard being right. Letting the
log crusher loose on a real `errors.ts`:

```
ORIGINAL              AFTER
 21   param?: ...      46  export class RateLimitError extends PlirinError {
 22   statusCode?...   47    /** Seconds until the limit resets ... */
 23   headers?: ...    48    readonly retryAfter?: number;
 ...                   ... [memor: omitted 2 lines] ...
```

Twenty-five lines of a class body gone. That is exactly the corruption the
guard exists to prevent, and the 80.9% figure mostly measures how destructive
the wrong compressor would be.

## Splitting file contents from reports

A file read is protected for a real reason: the agent will edit against it. A
report that quotes code has no such claim. Separating by origin (a known
`file_path`, or majority line-numbered output) and shape:

| bucket | tokens | payloads | removable |
|---|---|---|---|
| **report quoting code** | 526,557 | 455 | **399,475** |
| file contents (protect) | 480,642 | 153 | 416,902 |
| ambiguous | 65,018 | 77 | 51,563 |

**5.39% of context** sits in the reclassifiable bucket. That is the largest
single opportunity measured all day, larger than duplicates (1.78%), fetched
pages (1.08%) and the diff compressor as built (0.01%).

Spot-checking the largest members confirms they are not files:

```
11,410 tok  batch     '--- [1] browser ---'
10,399 tok  webfetch  'Fetched https://github.com/headroomlabs-ai/headroom'
 9,193 tok  webfetch  'Fetched https://docs.claude.com/en/docs/claude-code/hooks'
 7,909 tok  webfetch  'Fetched https://developers.openai.com/commerce/'
```

## Why the guard claims them

Taking the 9,193-token Claude Code docs page:

```
lines: 411
lines ending in ; { } ) : 93 = 23%      (below the 30% structural threshold)
code markers found: ['#!', '@', '```', 'if (', '} else']
looks_like_source: True
```

It is not the structural check. It is the two-marker rule: any payload
containing two of the marker set is called source, and a documentation page
with a fenced code sample contains a ``` fence plus almost inevitably one more.
The guard is doing what it was written to do; the rule is simply too coarse for
prose that embeds code.

## What a better classifier would key on

The distinguishing signal is not "contains code" but "is code":

- **Provenance.** A payload with a `file_path`, or one that is majority
  line-numbered, is a file read. `webfetch` and `browser` output never is.
- **Density.** A source file is code throughout. A docs page is prose with
  islands of code between fences, so the fenced regions can be measured
  separately from the whole.
- **Fences as boundaries rather than markers.** ``` currently counts as
  evidence *of* source; it is better read as a delimiter that separates prose
  from code, letting the prose compress while the fenced blocks stay verbatim.

That last one is the interesting design: it compresses the 77% of a docs page
that is prose while leaving every code sample byte-exact, rather than choosing
between protecting or crushing the whole payload.

## Honest limits

- 5.39% is removable mass, not a shipped saving. Today's record says the
  realised figure will be lower, and the fetched-page squeeze already measured
  7.4% on similar prose rather than the 40% guessed.
- The bucket split uses a heuristic (`file_path` present, or >50% line-numbered)
  that has not been validated against a labelled set.
- 153 payloads in the protect bucket carry 480,642 tokens. Any classifier change
  must be checked against those specifically, since misclassifying one of them
  is the failure the guard exists to prevent.


## Built: outcome

The classifier change shipped. Fetched documents are recognised by their own
provenance header and released from the guard unless they are majority fenced
code:

```
fetched documents released: 138
  before 408,531  after 400,748  saved 7,783  (1.9%)
  as share of ALL context: 0.10%
INTEGRITY violations (fenced code lost): 0
```

**0.10%, against the 5.39% of removable mass this document projected.**

The gap is the same one the diff compressor hit. Removable mass assumed the log
crusher would run on these payloads; in practice a released document classifies
as `text`, and `compress_plain_text` is deliberately lossless -- it strips
control characters and collapses blank runs, nothing more. So releasing the
payload grants permission to compress it, and the compressor that receives it
then declines on merit.

Two things were still worth it:

- **150 real file reads keep protection**, verified, and no file read in the
  corpus carries a fetch header, so the signal does not collide with the case
  the guard exists for.
- The remaining prose is now *reachable*. A prose compressor with real savings
  would apply to 408K tokens that were previously unreachable at any quality.

The honest reading is that the guard was not the binding constraint. The
binding constraint is that memor has no lossy prose compressor, and the
fetched-page squeeze measured earlier (7.4%) is the ceiling for one.
