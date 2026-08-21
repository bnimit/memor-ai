# Let `post_tool` optionally rewrite tool output (opt-in, fail-open)

## Summary

`post_tool` is an observer: spawned detached, stdout discarded
([`docs/HOOKS.md`](docs/HOOKS.md)). `pre_tool` can gate a call but only by
blocking it. So there is no way for an external program to *reduce* what a tool
result contributes to context.

This asks for an opt-in mode where `post_tool` stdout replaces the tool result,
with the same fail-open contract `pre_tool` already has.

## Why this cannot be done another way today

The usual answer is "point the agent at a local proxy." That is unavailable to
subscription users by design:

```rust
// crates/jcode-provider-anthropic-runtime/src/lib.rs:2003
let url = if is_oauth {
    API_URL_OAUTH                        // hardcoded const, line 61
} else {
    direct_transport.api_url.as_str()    // honours ANTHROPIC_BASE_URL
};
```

`direct_api_url()` (line 63) reads `JCODE_ANTHROPIC_API_BASE` then
`ANTHROPIC_BASE_URL`; the OAuth branch never calls it. Line 2010 likewise skips
`JCODE_ANTHROPIC_HEADERS` when `is_oauth`. Confirmed by running v0.79.1 with
`ANTHROPIC_BASE_URL` pointed at a local catcher: zero requests arrived, the turn
completed against the real API.

That is a reasonable default. A silent base-URL redirect of a subscription token
would be a genuine footgun. The consequence is just that OAuth users have **no**
context-reduction surface at all, and a hook is the only place left that does not
touch the token or the endpoint.

## What the change would look like

Mirroring the existing `pre_tool` machinery:

- new `post_tool_mutating = true` (or a separate `post_tool_filter` key) so the
  default stays fire-and-forget and costs nothing
- run synchronously with `post_tool_timeout_ms`, defaulting to something small
- exit 0 + empty stdout: unchanged, current behaviour
- exit 0 + stdout: stdout replaces the tool result
- anything else (non-zero, timeout, missing binary, unparseable): **fail open**
  to the original output, matching `pre_tool`'s stated rationale that a broken
  policy script should degrade to "no policy" rather than brick a session

## What it is worth, measured

I replayed a compressor over **71 real jcode sessions (7.15M tokens)** rather
than estimating. Request anatomy, measured on transcript content:

| slice | share |
|---|---|
| tool results | **59.9%** |
| tool-use args | 29.1% |
| reasoning | 5.7% |
| conversation | 5.2% |

Compressing only the tool output that is safe to touch:

```
fired on 181 results   233,459 -> 105,739 tokens
saved 127,720          (54.7% when it fires)
```

The rate when it fires is high; the realized figure is not, and the gap is
coverage rather than compressor quality:

```
tool_result tokens total  4,550,619   (10,947 results)
the hook engages on         233,459   (181 results)
coverage                        5.1% of tool output, 1.7% by count
```

Most tool output is declined on purpose: `read`/`edit` feed edits, failed
commands are what the user is debugging, and anything under ~2 KB is not worth
the risk of eliding something.

**The realized saving is ~1%.** Reconstructing every request as the transcript
prefix before each assistant turn (10,903 requests, mean 132K tokens, which
matches the mean of 3,992 real proxied requests in my own ledger) gives
**0.98%**. A resend-weighted estimate over the same corpus gives 0.96%. A
naive per-session figure gives 1.68%, and that one is optimistic: it counts
each payload once, while a real agent resends the whole transcript every turn,
and the resent mass is dominated by tool-use arguments, which are the code being
written and must never be touched.

So: worth having on a flat-rate plan, where the currency is context before the
5-hour window closes rather than dollars. Not a rate-limit fix, and I would
rather say so than have this closed as oversold.

Two constraints that argue the hook must be *allowed* to be conservative rather
than encouraged to be aggressive:

- Read/Grep/Glob results feed edits directly and must stay byte-exact.
- `batch` looked like the single best target (76% compressible) until I checked
  what it runs: 383 `bash` calls but also **171 `read`** on the same corpus. Its
  result embeds file reads, so compressing it would launder exactly the payload
  that must stay byte-exact.

## Prior art in jcode

`agentgrep` already does harness-level adaptive truncation based on what the
agent has seen, and the README presents that as a feature. This is the same idea
with the policy externalised, so tools other than grep can benefit without
jcode taking on the policy.

## Questions

- Is there a reason beyond the footgun that OAuth skips `direct_api_url()`? For
  what it is worth, Anthropic itself accepts an OAuth bearer relayed through a
  localhost proxy (I get identical `200`s direct and proxied), so the constraint
  appears to be jcode's own rather than upstream enforcement. Not asking for that
  default to change; a hook solves this without touching auth at all.
- Would you prefer a distinct `post_tool_filter` key over a flag on `post_tool`,
  to keep the observer path provably zero-cost?

Happy to send a PR if the shape is agreeable.
