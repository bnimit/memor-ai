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

- **148 real file reads keep protection**, verified, and no file read in the
  corpus carries a fetch header, so the signal does not collide with the case
  the guard exists for.
- The remaining prose is now *reachable*. A prose compressor with real savings
  would apply to 408K tokens that were previously unreachable at any quality.

The honest reading is that the guard was not the binding constraint. The
binding constraint is that memor has no lossy prose compressor, and the
fetched-page squeeze measured earlier (7.4%) is the ceiling for one.


## The prose compressor, sized

The 408K tokens the classifier made reachable, broken down by what is actually
in them:

| | tokens | share |
|---|---|---|
| ordinary prose | 137,889 | 33.8% |
| long prose lines | 86,363 | 21.1% |
| repeated lines (nav, footers) | 40,467 | 9.9% |
| URLs | 40,338 | 9.9% |
| short nav-ish lines | 15,342 | 3.8% |
| blank | 409 | 0.1% |

Three concrete lossy techniques, each measured on all 138 documents:

```
dedupe repeated lines     408,531 -> 373,014    8.69%
shorten long URLs         408,531 -> 398,877    2.36%
collapse blank runs       408,531 -> 407,219    0.32%
ALL THREE                 408,531 -> 362,590   11.25%
```

**11.25% of the reachable prose, which is 0.62% of total context.** Slightly
better than the 7.4% the earlier squeeze suggested, because URL shortening was
not in that experiment.

Note the URL figure is 9.9% of tokens, not the 28.6% an earlier line-level
bucket implied: that bucket attributed a whole line to "urls" whenever a URL
appeared anywhere in it. Same error as the `csv` and `html` retractions --
counting lines that *contain* a thing rather than the thing itself.

### Whether to build it

For: 0.62% is comparable to everything else shipped today (diff 0.01%,
classifier 0.10%), all three techniques are mechanical, and deduping a line
repeated three times inside one document is close to lossless.

Against: two of the three touch prose an agent may be reading for meaning.
Shortening `https://host/a/b/c?token=...` to `https://host/…` destroys a URL
the agent might need to fetch, and unlike a diff's context lines there is no
copy on disk to recover it from. That is the CCR case -- store the original,
hand back a marker -- rather than a plain elision.

Recommended shape if built: dedupe repeated lines (8.69%, the safe majority of
the win), skip URL shortening unless it goes through CCR, and skip blank
collapsing at 0.32% since `compress_plain_text` already does it losslessly.


## Re-checked after the hook fix

Two figures in this document were measured before the guard deferred to the
classifier, and one label was wrong. Corrected:

- The protect bucket is **148**, not 150. Two `jcode_docs` payloads were filed
  as file reads because their tool input carried a path; provenance now decides.
- Source-held mass is now **12.8% of context**, down from the 14.5% measured
  when this was written, because diffs and fetched documents route elsewhere.
- 0 of 148 file reads leak through the hook after the guard change.

The hook-path defect this document's fix originally had is worth recording as a
pattern rather than an incident. `detect_content_type` is not the only gate:
`posttool_compress` runs `looks_like_source` first, so routing a type correctly
was not enough to make it reachable. It happened twice in one session -- once
for fetched documents, once for diffs -- and is now fixed at the root by having
the guard ask the classifier instead of keeping its own exemption list.
