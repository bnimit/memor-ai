# Intercepting and compressing jcode tool output

Working answer to "how do we compress jcode's data", after every proxy-based
route turned out to be closed.

## Why not the proxy

jcode ignores `ANTHROPIC_BASE_URL` when the credential is OAuth. From
`crates/jcode-provider-anthropic-runtime/src/lib.rs:2003`:

```rust
let url = if is_oauth {
    API_URL_OAUTH                        // hardcoded const, line 61
} else {
    direct_transport.api_url.as_str()    // honours ANTHROPIC_BASE_URL
};
```

Confirmed by running v0.79.1 with `ANTHROPIC_BASE_URL` pointed at a local
catcher: zero requests arrived, the turn completed against the real API.

An `anthropic-compatible` provider profile *does* route through a proxy, but
that path requires an API key, and metered API billing costs more than the
flat-rate subscription it would replace. For a Max/Pro user the proxy is not
a viable route at all.

## What works

A **mutating `post_tool` hook**, added to jcode in
`0001-post-tool-filter.patch`. It never touches the token or the endpoint, so
it works identically under OAuth, and it edits context before the request is
built rather than rewriting a cached prefix afterwards.

Upstream status: implemented and verified locally, not yet submitted.
See `../jcode-post-tool-issue.md` for the write-up to file alongside it.

## Applying it

```bash
git clone https://github.com/1jehuang/jcode.git
cd jcode
git apply /path/to/memor/docs/jcode-patch/0001-post-tool-filter.patch
cargo build --release -p jcode --bin jcode
```

Needs rustc 1.94.1 or newer; 1.94.0 fails on an unrelated `aws-sdk-*`
requirement in the dependency tree.

## Wiring memor into it

`memor-jcode-filter.py` translates between the two contracts: jcode passes raw
tool output on stdin and reads the replacement from stdout, while memor's hook
speaks the Claude Code PostToolUse JSON shape.

```toml
# ~/.jcode/config.toml
[hooks]
post_tool = "/path/to/filter.sh"     # sh wrapper: exec python filter.py
post_tool_filter = true
post_tool_timeout_ms = 15000
```

Silence plus exit 0 means "no opinion", which is what makes the safety
defaults compose: memor declines `read`/`edit`, failed commands and small
payloads, and jcode keeps the original in every one of those cases.

## Verified

Controlled A/B against the forked binary, same command both times, using a
local SSE stub as the model so no subscription quota was involved. (jcode
always streams, `lib.rs:1224`, so a plain-JSON stub is not enough.)

Measured from jcode's own persisted session JSON, not from hook-side logging,
because the transcript is what gets resent on every later turn and therefore
where the saving has to survive:

| `post_tool_filter` | stored `tool_result` | lines | memor marker |
|---|---|---|---|
| `false` | 17,136 chars | 402 | absent |
| `true`  | **412 chars** | 11 | `[memor: omitted 392 lines]` |

**97.6% on this payload**, and the flag is the only thing that differs.

15 `hooks::` tests pass, including fail-open on non-zero exit, empty stdout,
missing binary, timeout, and runaway growth.

## What it is worth

About **1% of context per turn**, measured over 71 real jcode sessions by
reconstructing each request as the transcript prefix before each assistant
turn. The compressor achieves 54.7% on the payloads it engages, but it engages
on only 5.1% of tool-output tokens, because `read`/`edit` feed edits, failed
commands are what the user is debugging, and most payloads are small.

Worth having on a flat-rate plan, where the currency is context before the
rate-limit window closes rather than dollars. It is not a rate-limit fix, and
the honest reason to upstream it is the capability: today no jcode user on a
subscription has any way to reduce tool output at all.
