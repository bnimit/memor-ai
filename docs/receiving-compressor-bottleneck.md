# The receiving-compressor bottleneck

`next-milestone.md` ends on a finding it does not resolve: a perfect classifier
is worth between 0.03% and 4.04% of tool-result tokens, and which end you get
depends only on whether released payloads reach the log crusher or plain text.
This is the attempt to resolve it. The answer is negative, and the negative
result is worth more than the feature would have been.

## The shape of the problem

Two receivers exist, and nothing sits between them:

| | rate on its own bucket | why |
|---|---|---|
| `compress_log` | 70.2% | drops any line it does not recognise as important |
| `compress_plain_text` | 1.4% | deliberately lossless: control chars, blank runs, exact repeats |

The log crusher earns **88% of all realised savings**. Everything else combined
is 1.2% of tool-result tokens. So "release more payloads" only pays if they
land on the crusher, and the crusher is the one component that can silently
destroy an answer.

## Attempt 1: a middle-ground compressor

If the gap between 70.2% and 1.4% is the problem, build something in between.

Real candidate, measured rather than imagined: fold runs of same-*shape* lines
rather than byte-identical ones. `collapse_repeats` only catches exact repeats,
but benchmark tables, `ls -l`, dependency resolvers and progress records emit
lines that differ only in their numbers. Normalising digits and identifiers to
a skeleton catches all of them.

The text bucket does contain such content:

```
prose (<30% repeated shapes)      410 payloads   485,487 tok   41.4%
semi (30-60%)                     298 payloads   402,242 tok   34.3%
records (>=60%)                   170 payloads   285,129 tok   24.3%
```

Prototyped and run over the 400-session sample:

```
folded 118 of 878 payloads
1,172,858 -> 1,131,842
saved 41,016 = 3.5% of the text bucket = 0.52% of tool-result tokens
```

Against the 0.21% the lossless tidy already gets. **A net gain of 0.31%**, for
a new lossy compressor and the retention probe it would need. Not worth it.

The reason is the same one `text.py` already documents: the text bucket is what
*remains* after logs, search results and test output have been routed away, and
that residue is genuinely low-redundancy. 41.4% of it is prose.

## Attempt 2: release the 4.04% to the crusher

The larger prize. 443 payloads, 476,129 tokens, classified `source` but not
line-numbered, so not obviously file reads. Sending them to the crusher saves
319,248 tokens, 4.04% of tool-result.

It also **destroys 5,230 code lines across 273 of the 443 payloads.**

The reason is immediate once the commands are printed:

```
95 lines  $ cd .../node_modules/yjs14/src/ && cat ...
93 lines  $ git show ca906ea75:engine/runtime/action-sink/src/output.rs
81 lines  $ sed -n '1,260p' apps/web/.../table.tsx
80 lines  $ sed -n '1,200p' apps/web/src/components/dashboard/BalanceHero.tsx
```

These are file reads. They lack line numbers because they came through `bash`
rather than the `Read` tool, and the classifier's only evidence for "this is a
file the agent will edit against" was the line-number prefix. **The guard was
right and the classification was right; the payloads really are source.**

## Attempt 3: allowlist the commands

If `sed -n`, `cat`, `git show` and `head` produce file dumps, detect them from
the command string and release only the rest.

First detector was wrong in a way worth recording, because it flattered the
result: `\bcat\s` does not match `cat /Users/...` the way I assumed under a
`;`-joined pipeline, and `\btail\s` missed `tail -60 file.go`. Splitting on the
corrected pattern moves most of the mass:

```
file-dump command   393 payloads   402,875 tok   saved 3.57%   code lines lost 5,091
other               50 payloads     73,254 tok   saved 0.47%   code lines lost   139
```

So the safe remainder is 0.47%, not 4.04%. And it is still not safe: printing
the 10 residual payloads shows every one is a file dump the pattern cannot see.

```
$ find apps/web/src/components/ui -iname "button.tsx" -exec cat {} \;
$ cd ... && python3 - <<'EOF'   (a heredoc that prints a file)
$ cd ...; echo "=== backoff.go ==="; cat provider/backoff.go
$ grep -rln "format_error" ... ; echo "----" ; ...
```

A command line is not a reliable signal for what its output contains. Any
allowlist is a guess about shell semantics, and the failure mode is silent
corruption of a file the agent is about to edit.

## Attempt 4: make the crusher refuse to drop code

The right axis. Stop asking *which payloads* are safe to release, and fix the
property that makes release dangerous: the crusher deletes any line it does not
recognise as important. Pin code-looking lines as important, then release
everything. Content-based, no shell parsing.

```
plain log crusher    saved 4.04% of tool-result   code lines lost: 5,230
code-safe crusher    saved 3.53% of tool-result   code lines lost:     0
```

3.53% with zero loss. This looked like the answer.

**It is circular.** Lines were pinned with a regex and loss was measured with
the same regex, so zero was arithmetically guaranteed. Re-checking with five
predicates the pinner never saw:

```
independent predicate   plain crusher   code-safe
indented+punct       10,988/15,611     8,371
assignment            1,572/2,136      1,563
call w/ args          5,384/7,864      4,357
type/sig              2,153/3,005      1,721
string literal        4,457/6,316      3,771
```

The code-safe crusher still drops **8,371 indented code lines and 1,563
assignments**. It moved the loss out of the measurement, not out of the output.
The pinning regex keys on line-initial keywords, and most lines of real code
are continuations, closing punctuation, and indented statements that begin with
none of them.

## Conclusion

The bottleneck is not a missing compressor. It is that **the 4.04% is source
code**, and the only two things one can do with source code are pass it through
or lose some of it.

The log crusher's 70.2% is not a technique that could be generalised; it is
what you get for deleting most of a payload, which is correct for a log because
a log's answer is a handful of lines, and wrong for a file because a file's
answer is all of it.

Three specific traps, all of which produced an encouraging number first:

1. **A middle-ground compressor** measures 0.52% against an existing 0.21%.
2. **A command allowlist** cannot see `find -exec cat`, heredocs, or a `cat` on
   the third line of a pipeline. Its failure is silent.
3. **Pinning code lines** can be made to show zero loss by measuring with the
   pinning predicate. It requires an independent check to falsify, and it does
   not survive one.

What this leaves: the honest ceiling for the source bucket is close to zero,
and the ranking in `next-milestone.md` stands — widen what reaches the crusher
only where the payload is genuinely *not* a file, and expect single-digit
fractions of a percent.

The one direction not yet exhausted is the **2KB floor**: 18.1% of tool-result
tokens sit beneath it, untouched, and unlike everything above they carry no
correctness hazard, because the compressors that would receive them are the
same ones already trusted on larger payloads.
