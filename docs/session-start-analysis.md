# Session start: measured, and smaller than it looked

Follow-up to the cache economics in `compression-gap-analysis.md`. The theory
was that a resumed session re-establishes its whole transcript as a *cold*
prefix, which bills at 1.25x with no read discount, and that compressing there
escapes the cache-write penalty that makes the proxy path decline. The theory is
right. The size of the prize is not what I guessed.

## The ledger cannot answer this

```
anthropic rows       5,391
  with cache_creation   10
  with cache_read    1,452
rows with session_id   122 of 5,564
```

Cache-creation is recorded on 10 rows and `session_id` on 122, so cold starts
cannot be identified from the ledger. Everything below is measured from
transcripts instead.

## Cold prefixes are large and common

Tokens a session would re-establish if resumed:

| | sessions | median | mean | p90 | max |
|---|---|---|---|---|---|
| jcode | 84 | 40,879 | 88,588 | 150,443 | 936,312 |
| Claude Code | 2,456 | 17,910 | 32,890 | 53,918 | 4,684,713 |

43% of jcode sessions and 12% of Claude Code sessions would re-establish more
than 50k tokens. That part of the theory holds: these are real, and each one
bills at the expensive rate.

## But the compressible share is small

Running the existing compressors over the 25 largest jcode sessions, with the
cache constraint lifted so *every* payload is eligible (the freedom a cold
prefix buys):

```
before   5,687,432
after    5,429,204
saved      258,228   (4.5%)
```

Not the ~30% assumed. The reason is the composition:

| slice | tokens | share |
|---|---|---|
| tool results | 3,229,816 | 56.8% |
| tool_use args | 2,101,098 | 36.9% |
| text | 356,518 | 6.3% |

`tool_use` arguments are 36.9% and are the code being written, so they are
untouchable. Of the 56.8% that is tool results, most is source code held by the
guard or payloads under the size floor. Lifting the cache constraint does not
change what is *safe* to compress, only what is *economic* to compress, and
safety was already the binding constraint.

## Worth it anyway, for a different reason

4.5% of a cold prefix, billed at 1.25x, is worth about 1.4x the same percentage
saved on warm traffic. On the 25 sessions measured that is ~323,000
base-token-equivalents.

More importantly it is the only path where compression is unambiguously
profitable: no cached prefix exists, so there is no write penalty and no
break-even threshold to clear. Every token saved is simply saved.

## Honest limits

- One machine, one user's traffic.
- The 4.5% is what *today's* compressors achieve. A diff compressor would add to
  it, since diffs are currently declined by the source guard.
- No resumed sessions were found in the local data (`parent_id` was null on all
  84 jcode sessions), so the frequency of resumes is unmeasured. The prefix
  sizes are real; how often they are paid is not.
