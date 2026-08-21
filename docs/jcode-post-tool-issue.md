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
than estimating. Request anatomy:

| slice | share |
|---|---|
| tool results | **59.9%** |
| tool-use args | 29.1% |
| reasoning | 5.7% |
| conversation | 5.2% |

Result of compressing only tool output that is safe to touch:

```
fired on 181 results   233,459 -> 105,739 tokens
saved 127,720          (54.7% when it fires)
whole-request saving   1.79%
answer-critical retention  94.7%
```

**1.79% of a turn** is the honest figure, not a headline number. It is worth
having on a flat-rate plan, where the currency is context before the 5-hour
window closes rather than dollars, but I would not oversell it.

Two things that constrain it, both of which argue the hook must be *allowed* to
be conservative rather than encouraged to be aggressive:

- Most tool output must not be rewritten. Read/Grep/Glob feed edits directly.
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
