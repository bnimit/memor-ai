# What memor declines, and what is actually worth building

Measured on 11,337 real `tool_result` payloads (4.67M tokens) from local jcode
sessions. Written down because a first pass at this produced numbers that were
wrong by an order of magnitude, in the optimistic direction.

## The denominator

```
ALL tool_result tokens        4,671,162  (11,337 payloads)
  under 2KB, never attempted  1,337,570   28.6%
  >=2KB, compressor GAINS     1,010,004   21.6%
  >=2KB, compressor DECLINES  2,323,588   49.7%
```

The 2.32M is the *declined* slice, not "memor did nothing". It compresses 1.01M
tokens of the same corpus. Most declines are deliberate: source code, failed
commands, payloads too small to be worth the risk of eliding something.

## First pass: wrong

Pattern-matching the declined slice against the content types Headroom ships and
memor does not:

| candidate | payloads | tokens | share of declined |
|---|---|---|---|
| diff | 663 | 909,140 | 39.1% |
| csv / tabular | 317 | 582,080 | 25.1% |
| html | 65 | 252,801 | 10.9% |

Every one of these is inflated, because a loose regex matches *payloads
containing a pattern*, not *compressible content*.

## Second pass: measured

**Diff — real, but 16% not 39%.** Counting lines by role across 59 genuine diff
payloads:

```
CHANGED (+/-) lines       80,695   58.2%   must keep, untouchable
context lines             36,386   26.3%   recoverable from disk
headers                   10,164    7.3%
hunk markers               5,348    3.9%
```

Only unchanged context is safely droppable, so the ceiling is 26.3% and the
realistic figure keeping 3 lines per hunk is **~16%**, about 145K tokens on this
corpus rather than 909K.

There is also a parsing hazard. 390 lines inside those payloads are not diff
syntax at all — `## Commits`, `Author: ...`, `... (output truncated)`, commit
subjects from `git log`. A parser that treats every non-`+`/`-` line as
droppable context deletes commit messages and file lists, which is the same
class of bug as the manifest regression fixed in `b6a7ac8`.

**Tabular — not real.** The `csv` regex was matching source code that contains
commas: `import { Tabs, TabsContent, TabsList } from ...`. Requiring a
*consistent* column count across at least 70% of lines:

```
payloads with genuine table structure:  3      (3,881 tokens)
payloads with commas but no structure:  9
```

Two of those three are a progress bar and a JSON log line. There is no tabular
workload here.

**HTML — not real.** 116 payloads contain HTML-ish tags and 406K tokens, but
they are `.tsx` component files at 0.1–0.5 tags per line, every one classified
`source` and held by the source guard. An HTML extractor would either do nothing
or break the guard.

## Conclusion

Of the three candidates, one survives contact with the data, at roughly a third
of its advertised size:

| | first pass | measured |
|---|---|---|
| diff | 909K tokens | **~145K** |
| tabular | 582K tokens | ~4K |
| html | 253K tokens | 0 |

A diff compressor is still the best remaining compression work: the removable
part is unchanged context that exists on disk, which is a stronger safety
argument than anything already shipped. It needs strict parsing that bails on
anything not unambiguously a hunk body, and the same answer-critical retention
probe used on the log compressor.

The general lesson: pattern presence is not compressibility. Both of the
retracted numbers came from regexes that fired on source code, which is exactly
the content the source guard exists to protect.
