```
                                                _
 _ __ ___   ___ _ __ ___   ___  _ __       __ _(_)
| '_ ` _ \ / _ \ '_ ` _ \ / _ \| '__|____ / _` | |
| | | | | |  __/ | | | | | (_) | | |_____| (_| | |
|_| |_| |_|\___|_| |_| |_|\___/|_|        \__,_|_|

  Measured memory and opt-in token savings for coding agents.
```

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-1631%20passing-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)]()
[![PyPI](https://img.shields.io/pypi/v/memor-cli.svg)](https://pypi.org/project/memor-cli/)

**One long-term memory shared by every LLM coding tool on your machine.** A
decision recorded while using Claude Code is there when you switch to Cursor,
Codex, jcode, Goose or Kimi. Everything runs locally. No Memor API key required.

Two things, in one install:

1. **Memory** — one store, every tool. Recall past decisions and bugfixes so you
   stop re-explaining your own codebase, in whichever agent you happen to open.
2. **Compression** — crush noisy tool output before it reaches the model,
   without losing what you need from it. This pays for the memory: it removes
   more tokens than recall injects.

### What makes it different: nothing has to cooperate

Other shared-memory tools need the model to call a `save` tool, or a plugin
written per harness, or they capture your screen. memor reads the transcripts
your agents already write to disk for their own reasons.

The practical consequence: **Goose has contributed 1,256 memories to this
machine's store and has never heard of memor** — no plugin, no MCP
registration, no entry in its config. It contributed by being used.

One developer's store, as an illustration rather than a benchmark:

| Writes memories | | Reads them back | |
|---|---|---|---|
| `claude_code` | 24,446 | `claude` | 3,635 |
| `jcode` | 1,300 | `cursor` | 427 |
| `goose` | 1,256 | `codex` | 38 |
| `kimi` | 95 | `jcode` | 32 |

Read those totals with a date attached. They are lifetime figures, and a
lifetime total spans every bug it outlived: `codex`'s 38 reads are all from
June, before a fix that taught the proxy which project to search, so they
describe code that no longer runs. `memor doctor` reports the same numbers
windowed against the last behaviour change, which is the honest view.

Scope is by **project**, never by agent, so a memory crosses tools by default
rather than by configuration. Note the asymmetry: writing needs only a
transcript on disk, while reading needs an integration, which is why Goose and
Kimi appear as writers and not yet as readers.

### What gets remembered, and what deliberately does not

Memory is built from **sessions**, automatically, because they are a byproduct
nobody else keeps: once a conversation scrolls away, the reasoning in it is gone. Why a
library was rejected, what was tried and abandoned, the correction you made at
4pm — none of that survives in the repo.

It does **not** crawl your codebase, and that is a design choice rather than a
missing feature. A design doc, an ADR, a README are live files the agent can
already read. Copying them into memory creates a stale duplicate of an
authoritative source, and a stale memory is worse than none: it costs tokens
*and* misleads.

For notes that live outside the repo — an onboarding brief, an incident
writeup, a decision record in a wiki — point the daemon at the folder and it
keeps them ingested the same way it keeps transcripts:

```bash
memor docs watch ~/notes        # ingested on every poll, like a session store
memor docs list                 # what is watched, and what reached the store
```

Watching is safe because chunk ids are content-hashed: an unchanged file
re-reads to the same rows for free, and a chunk that no longer appears in the
file is retired rather than left to answer from a deleted draft. Nothing is
auto-discovered — `document_dirs` starts empty, because indexing a repo's own
`docs/` duplicates files the agent can already open.

Files whose names announce credentials — `backup-codes`, `recovery_codes`,
`2fa`, `password`, `.env`, `id_rsa` — are skipped entirely rather than
redacted. Redaction matches *structured* secrets (an `sk-` key, a JWT, a PEM
block); a page of 2FA recovery codes is bare digits and passes straight
through, so the decision is made before the file is read.

`memor ingest-doc <file> --project <name>` still exists for a one-shot import,
but it does not notice later edits. Secrets are redacted on the way in, the
same as every other ingest path.

### Is the memory half working? Partly, and it now says so

Compression has always been easy to measure. Memory was not: until recently
there was no answer to the product's own central question.

**The whole-product ledger**, from this machine's store:

```
compression saved   2,453,627 tokens
recall injected     1,146,846 tokens   (what memory costs)
                    ---------
net                 1,306,781 tokens
```

Recall spends 47% of what compression saves. That is the design: compression
funds the memory layer.

Both figures are **tokenizer estimates, not billed measurements**. Memor counts
the payload before and after it rewrites it; no provider invoice confirms the
difference. For the hook path that gap is permanent by construction, since the
payload is shrunk before the agent ever builds a request, so the provider never
saw the original. Proxy traffic *can* be grounded, because the provider reports
usage per request, and `memor compression-worth` says which of the two a given
figure rests on rather than blending them.

**Cross-tool recall is now graded.** Until 2026-08-23 the feedback loop ran for
Claude only, so every cross-tool recall was served and never judged — the one
capability memor exists for was the one it could not measure. All four agents
are now read. The dashboard shows the reader × writer matrix at
`/api/cross-tool`, with cross-tool and same-tool kept apart because blending
them hides the comparison that matters.

| | state on this machine |
|---|---|
| Same-tool recalls | **170 of 170 judged** used |
| Cross-tool recalls | **37 awaiting a verdict** |

The cross-tool column fills in as each agent's next session is ingested. It
reads *"not yet graded"* rather than 0%, because "nothing has been judged" and
"judged and useless" are different facts.

**Two caveats worth stating.** The 170 judged verdicts are 0.5% of a
34,039-artifact store, so every claim about memory quality rests on a thin
sample. And the rejection detector was blind until 2026-08-23 — it matched
phrases like *"that's incorrect"* while real users push back by asking *"didn't
we already fix that?"* — so scores recorded before then counted hits with no
misses.

### What the compression numbers actually are

Compression is easy to verify and therefore easy to falsify, so these are measured on real traffic rather than favourable fixtures:

| Measurement | Result | Measured on | Reproduce |
|---|---|---|---|
| Compression on tool output | **11.7%** saved | all tool output across 6 large real sessions | `memor request-anatomy` |
| ...on the payloads it engages | **43.3%** saved | the subset it does not decline, same 6 sessions | `memor request-anatomy` |
| Answer-critical retention | **96.2%** kept | 132 grounded cases from real edits | `memor eval-retention` |
| ...against truncation at a comparable budget | **50.3%** kept | the same 132 cases | `memor eval-retention` |
| Retrieval accuracy | **95.0%** any-hit, 86.7% all-gold | LongMemEval_S, n=120 — see the caveat below | `memor eval-longmemeval` |
| Tool-output compression, when it fires | **47.7%** saved | 70 of 291 large Bash results, 60 sessions | `memor hook-worth` |
| ...across all Bash output | **11.1%** saved | the same 291 results (24.1% coverage) | `memor hook-worth` |
| Proxy, blended over all traffic | **0.8%** | 5,414 real proxied requests | dashboard |

**Why the blended figure is small, and why that is arithmetic rather than a
weak compressor.** A coding-agent request is mostly things memor must not touch:

| Slice of a request | Share | Why |
|---|---|---|
| Tool-use arguments | **42.7%** | the code being written; eliding it corrupts edits |
| Tool results | **33.0%** | the only slice memor rewrites |
| Conversation text | **23.9%** | rewriting it re-forms the provider's cached prefix |
| Images | 0.4% | billed by dimensions, not bytes |

At 11.7% across all tool output, a whole request comes down by **3.9%**. Even
at the 43.3% rate achieved on payloads the compressor engages, the ceiling for a
whole request is 14.3% — and that higher figure describes a subset, not your
bill.
Any product claiming more than that on this shape of traffic is measuring a
different denominator — typically log-heavy or document-heavy workloads where
tool output is most of the request. `memor request-anatomy` prints this
breakdown for your own sessions.

> **What LongMemEval does and does not show.** It scores whether retrieval
> surfaced the right session, not whether the agent then answered correctly, and
> its content is conversational personal-assistant memory rather than coding
> work. It is evidence the retriever functions; it is not evidence that memor
> makes a coding agent better. The honest end-to-end number is worse and is
> published in the changelog: a counterfactual win rate that read 63.8% before
> the harness was corrected to call production `recall()`, and 8.6% after.
>
> No harness here runs an agent at a task and checks whether it succeeded, so
> memor cannot currently claim it makes agents more effective, in either
> direction. `docs/plans/2026-09-06-task-outcome-benchmark-design.md` sets out
> what such a benchmark would have to look like and why SWE-bench cannot be
> borrowed for it: its instances carry no prior session history, so a memory
> layer has nothing to remember and scores as baseline by construction.

> Beware the image trap. A base64 screenshot tokenises as a vast string — a
> 161 KB PNG counts as 113,367 tokens if you feed the encoded text to a
> tokenizer — but providers bill an image by its dimensions. Counting the base64
> put images at 31% of a session and made them look like the biggest prize
> available; they are 0.4%. If an estimate implies more tokens than the provider
> ever billed, the estimate is wrong.

**Coverage is capped by safety, on purpose.** The hook fires on 21.9% of large
Bash results. Nearly all of the rest is held back by the source guard — output
that is source code, most often a heredoc or a `cat`, and an agent editing
against a mutilated read is a worse outcome than any token saving is worth.
Pushing coverage higher means weakening that guard.

Read, Grep and Glob results are never touched at all and are excluded from
these figures rather than counted as missed coverage: they feed edits directly,
so the hook declines them by design rather than by failure.

None of these are numbers you have to take on trust. `memor hook-worth` replays
the hook over your own sessions and prints the same breakdown, including the
denominator it used.

**Savings are reported net of the provider's prompt cache.** Rewriting a payload
that was being served from cache turns cheap cache reads into full-price cache
writes, so a gross saving can be a net loss. Memor reads usage out of the
response stream and prices cache writes against reads; where the provider never
reported usage it says *unmeasured* rather than assuming zero.

> Usage arrives inside the response stream, so a proxy that has been running
> since before this landed reports nothing. `memor service status` says so
> explicitly when the running process is older than the installed code, and
> `memor service restart` fixes it.

---

## Quick Start

```bash
# Install globally (recommended)
pipx install memor-cli

# Install the hook + download embedding model (~60MB)
memor install-hook                  # interactive — pick an agent
memor install-hook --agent kimi     # or pass directly (claude, codex, copilot, kimi, goose, jcode)

# Compress noisy tool output before it reaches the model (Claude Code)
memor install-compress-hook         # restart Claude Code afterwards

# Start as a background service (macOS/Linux)
memor service install

# Or run in the foreground
memor daemon
```

That's it. Every prompt now gets automatic context recall, and noisy command
output is crushed before it reaches the model. Check what that was worth on your
own traffic at any time:

```bash
memor compression-worth             # realized savings, coverage, and net of cache
memor doctor                        # is each wired agent still reading memory?
```

`memor service install` also starts the dashboard as a background service, so it's already live at http://localhost:8420 (and is recycled whenever you stop/restart/uninstall the service). To run it in the foreground instead:

```bash
memor dashboard
# Opens http://localhost:8420
```

> **Alternative install:** `pip install memor-cli` works too — just make sure `~/.local/bin` is on your PATH so the `memor` command is available.

### Optional: Token savings proxy

Two independent compression paths, usable together or alone.

**Tool-output compression (Claude Code, no proxy):**

```bash
memor install-compress-hook          # PostToolUse; restart Claude Code after
```

This crushes Bash output *before* it enters the transcript, which is where the
compressible bulk actually is. Measured on this repo's own test suite, a
`pytest -v` run goes from 1,975 to 222 tokens (88.8%) with the pass/fail summary
and every failure diagnostic intact. Because the payload is shrunk on the way
in, no already-cached prompt prefix is rewritten, so there is no cache
re-formation cost to weigh against the saving.

Scope is deliberately narrow: Bash output only. Reads, greps and source code
are never rewritten, and a command that exits non-zero passes through whole —
a failing build is when every line matters most. Savings land in the same
ledger the dashboard reads.

**Full request compression (proxy):**

Memory works out of the box via hooks. To also compress tool payloads and track token savings:

```bash
memor install-proxy --agent claude   # or: codex, goose, kimi, cursor, cline, opencode
```

This points your agent at a local proxy on `127.0.0.1:8421`, compresses tool payloads before they reach the provider, and logs savings to the dashboard. For proxied agents, hooks skip inject and **recall is served by the proxy instead**; Cursor and Copilot always use hooks only.

A proxy is handed an HTTP request and nothing else — no working directory — so it works out which project a request belongs to by reading the request itself: the working directory the agent states in its system prompt, and failing that the git root shared by the absolute file paths its tool calls name. If neither yields a real repository, recall falls back to global memories rather than guessing at a project.

`memor service restart` (e.g. after `pipx upgrade`) keeps the proxy running when it was opted in. If the proxy fails its health check on install, Memor restores your agent’s original API URLs so calls are not left pointing at a dead localhost port. `memor service stop` warns while agents still point at the proxy; `memor service uninstall` restores direct API configs for proxy-enabled agents.

To revert: `memor uninstall-proxy --agent claude`

> **Codex support is experimental.** The proxy implements the OpenAI Chat Completions API (`/v1/chat/completions`). Codex CLI may instead use the Responses API (`/v1/responses`) depending on version and model, in which case requests will not route through the proxy and you will see no savings. Memory via hooks is unaffected. Track it in [#26](https://github.com/bnimit/memor-ai/issues/26).

### Optional: Full Cursor install (recommended)

**Memory alone** still only needs `memor install-hook` (Claude covers Cursor), and
Cursor's own sessions are ingested from its local composer store whether or not
anything is installed. For token savings on Cursor, one command enables the full
stack:

```bash
memor install-proxy --agent cursor
```

That flow:

1. Explains what will be installed, then asks you to confirm  
2. Installs memory hooks (if missing) + Shell compress hooks + BYOK on `:8421`  

Flags: `--yes` to accept without prompting.

```bash
memor uninstall-proxy --agent cursor   # restore original settings
memor service restart                  # recycle services after an upgrade
```

Compression reaches Cursor through the **Shell compress hooks**, which crush terminal
and tool output before Cursor ingests it. Subscription Composer traffic is not
intercepted — see [Why no Composer interception](#why-no-composer-interception).

---

## Dual-Path Architecture

Memor runs two complementary local paths — combine them or use either alone:

```
                         ┌──────────────────────────────────────┐
                         │        Memor (localhost)             │
                         │  daemon · hooks · proxy · dashboard  │
                         └──────────────────┬───────────────────┘
                                            │
              ┌─────────────────────────────┼─────────────────────────────┐
              ▼                             ▼                             ▼
       Path A: Hooks                 Path B: Proxy                 Shared store
       (memory, all agents)          (memory + savings)            SQLite + vec
              │                             │                      + FTS + ledger
              │ recall inject               │ recall inject +
              │ always on after             │ compress tool payloads;
              │ install-hook                │ forward; CCR originals
              ▼                             ▼
       Claude · Cursor · Codex         Anthropic / OpenAI
       Copilot · Kimi · Goose          (your existing credentials)
       Jcode (ingest + MCP)
              ▲
              │
       Daemon ingests sessions ── Claude ~/.claude/projects/
                                  Codex  ~/.codex/sessions/
                                  Cursor Cursor composer store (SQLite)
                                  Kimi   ~/.kimi/sessions/
                                  Goose  ~/.local/share/goose/...
                                  Jcode  ~/.jcode/sessions/
                                  Docs   watched folders (memor docs watch)
```

| Path | Purpose | Default |
|------|---------|---------|
| **Hooks** | Shared memory recall across all agents | On after `memor install-hook` |
| **Proxy** | Recall + compress tool payloads; ledger token savings | Opt-in via `memor install-proxy` |

**Memory is fire-and-forget** — install hooks once, every prompt gets relevant context. **Proxy is opt-in** — for Claude Code, Codex, Goose, and Kimi when you want measurable token savings on top. Proxied agents skip hook inject and are served recall by the proxy instead; Cursor and Copilot always use hooks. The proxy forwards your existing Anthropic/OpenAI credentials; Memor does not require its own API key.

Both paths write to the same recall ledger, including recalls that return nothing — a retrieval that found no match is the only direct evidence that retrieval was asked a question it could not answer, so it is recorded rather than discarded.

---

## How It Works (hooks path)

```
  You type a prompt
  (Claude · Cursor · Codex · Copilot · Kimi · Goose · Jcode)
      |
      v
  Hook fires — auto-detects which agent
      |
      v
  Embed query locally (model2vec, ~2ms)
      |
      v
  Hybrid retrieval: dense vectors + lexical BM25, fused (RRF)
      |
      v
  Relevance gate drops off-topic matches (inject nothing if nothing fits)
      |
      v
  Rank: similarity + recency + kind weight + quality
      |
      v
  Inject relevant context into prompt
      |
      v
  Your agent sees past decisions, bugfixes,
  architecture choices — without you re-explaining
```

### Agent matrix

| Agent | Memory (hooks) | Proxy / savings |
|-------|----------------|-----------------|
| **Claude Code** | Yes | Yes — `memor install-proxy --agent claude` |
| **Codex CLI** | Yes | Experimental — `memor install-proxy --agent codex` (Chat Completions only) |
| **Cursor** | Yes — own ingest, plus hooks | BYOK proxy + Shell compress hooks |
| **Copilot CLI** | Yes | No — hooks only |
| **Kimi CLI** | Yes | Yes — `memor install-proxy --agent kimi` |
| **Goose** | Yes | Yes — `memor install-proxy --agent goose` (auto-detects common Desktop custom providers like `custom_deepseek`; use `--upstream-url` if yours is custom) |
| **Jcode** | Ingest via hooks; recall via MCP — `memor install-hook --agent jcode` then `memor install-mcp --agent jcode` | Yes — via a `PATH` shim, see [Jcode compression](#jcode-compression) |
| **Cline** | No | Yes — `memor install-proxy --agent cline` |
| **OpenCode** | No | Yes — `memor install-proxy --agent opencode` |

### Jcode compression

Jcode has no hook that can rewrite tool output — `post_tool` is a detached
observer whose stdout is discarded — and it ignores `ANTHROPIC_BASE_URL` when
the credential is OAuth, so neither of memor's usual paths reaches it.

What does reach it is the shell. Jcode's bash tool resolves `bash` through
`PATH`, so a shim earlier on `PATH` sees tool output before jcode stores it,
with the official binary untouched and still upgradable:

```bash
mkdir -p ~/.memor/shim
cp docs/jcode-patch/memor-jcode-shim.sh ~/.memor/shim/bash
cp docs/jcode-patch/memor-jcode-filter.{sh,py} ~/.memor/shim/
chmod +x ~/.memor/shim/bash ~/.memor/shim/memor-jcode-filter.sh
PATH="$HOME/.memor/shim:$PATH" jcode
```

Measured on stock `jcode v0.79.1`, reading the stored transcript: a 400-line
build log lands as 17,136 characters without the shim and 370 with it. Set
`MEMOR_SHIM_OFF=1` to disable it without editing `PATH`.

The shim is byte-exact for everything else — `seq`, `echo`, `printf` without a
trailing newline and a full source file all hash identically to real bash, and
exit codes and stderr pass through. Only `bash -c` and `bash -lc` are
intercepted, so interactive and login shells are untouched, and if memor is
not importable it execs the real shell unchanged.

`docs/jcode-patch/` also carries a patch adding a proper mutating `post_tool`
hook to jcode, for anyone willing to build from source. The shim exists because
running a fork means giving up upstream upgrades.

### Hook install details

| Agent | Hook protocol | Config location | Install |
|---|---|---|---|
| **Claude Code** | `UserPromptSubmit` + `additionalContext` | `~/.claude/settings.json` | `memor install-hook --agent claude` |
| **Codex CLI** | `UserPromptSubmit` + `additionalContext` | `~/.codex/hooks/hooks.json` | `memor install-hook --agent codex` |
| **Copilot CLI** | `userPromptSubmitted` + `additionalContext` | `~/.copilot/hooks/memor.json` | `memor install-hook --agent copilot` |
| **Cursor** | `beforeSubmitPrompt` + `additionalContext` | `~/.claude/settings.json` (loaded as Claude user hooks) | automatic — covered by the Claude install |
| **Kimi CLI** | `UserPromptSubmit` + plain-text context | `~/.kimi/config.toml` | `memor install-hook --agent kimi` |
| **Goose** | `UserPromptSubmit` + `additionalContext` | `~/.agents/plugins/memor/` | `memor install-hook --agent goose` |
| **Jcode** | `turn_end` / `session_end` (ingest only — jcode hooks are observers and cannot inject) | `~/.jcode/config.toml` | `memor install-hook --agent jcode` |

A single `memor-hook` binary auto-detects which agent is calling it — no separate entry points needed. Kimi and Goose installs stamp `MEMOR_HOOK_AGENT` so Claude-shaped payloads stay correctly labeled. Cursor loads the same Claude user hooks, so installing for Claude Code covers Cursor too. When an agent is proxied, its hook skips inject and memory comes from the proxy path; Cursor and Copilot always inject via hooks. The dashboard tracks recalls per agent so you can see usage across all your environments.

> **Goose note:** Memory inject needs a Goose build with advise-tier `additionalContext` support. DeepSeek (or any other provider) is configured inside Goose — Memor talks to Goose's hooks, not to the model provider.

> **Jcode note:** Jcode is the one agent whose read and write paths are split, because every jcode hook except `pre_tool` is a detached observer: it fires and forgets, and its stdout is discarded. That makes hooks an excellent *ingest* trigger — `turn_end` carries the session id and cwd, and a slow ingest can never delay your turn — but it leaves no channel to inject memories into a prompt. Recall is therefore served by MCP, as a `memor_recall` tool the model calls for itself:
>
> ```bash
> memor install-hook --agent jcode   # writes: jcode work becomes memory
> memor install-mcp  --agent jcode   # reads: memor_recall tool
> ```
>
> Restart jcode afterwards so it loads the MCP server. Pass `project=` explicitly when calling `memor_recall`: the MCP server's working directory is whatever launched the agent, so the default is often not the project you are in. A miss names the projects that do have memories.

> **Note:** Cloud-hosted agents (Codex cloud, Copilot cloud agent) run in remote sandboxes and cannot reach local hooks. MCP server support for sandboxed agents is planned ([#26](https://github.com/bnimit/memor-ai/issues/26)).

**Background processes** (supervised by `memor service install`):

1. **Daemon** — polls local agent session stores (Claude Code `~/.claude/projects/`, Kimi `~/.kimi/sessions/`, Goose `~/.local/share/goose/sessions/sessions.db`), embeds chunks, runs distillation, analyzes feedback (Claude), promotes cross-project patterns to global scope, compacts duplicates, auto-compacts the vector index when bloated, tracks session-level token usage. Model providers are not ingest sources — only the agent that owns the session. All local. Use `memor backfill` for a one-shot ingest of past sessions.
2. **Hook** — fires on every prompt, recalls relevant memories, injects them as context. Works across Claude Code, Cursor, Codex, Copilot, Kimi, and Goose. Measured on 2,285 real recalls that returned hits: median 176 ms, 90th percentile 3.1 s. The tail was concurrent prompts queueing behind one another in the sidecar, now fixed; the ledger figure still includes recalls served before that. A single recall is ~120 ms. A recall that finds nothing returns in about 214 ms, because the relevance gate rejects before the lexical channel runs. The embedding itself is well under a millisecond; the time is retrieval and ranking over a store this size.
3. **Proxy** (optional) — intercepts Anthropic/OpenAI API calls on `127.0.0.1:8421`, serves recall, compresses tool payloads, forwards to your provider, and writes savings and recall ledgers. Started automatically by `memor install-proxy`.

**No Memor API key required.** Embeddings and compressors run locally. The proxy forwards your existing Anthropic/OpenAI credentials — keys are never stored. Vectors stored in [sqlite-vec](https://github.com/asg017/sqlite-vec). Everything runs on your machine.

---

## Hybrid Retrieval

Memor retrieves over two channels and fuses them, so it catches both semantic matches and exact terms:

- **Dense** — local vector similarity (model2vec) for semantic recall.
- **Lexical** — SQLite FTS5 / BM25 over the raw text, to recover exact identifiers, error strings, and API names that static embeddings blur together.

The two rankings are combined with **Reciprocal Rank Fusion (RRF)**. A **relevance gate** drops anti-correlated (off-topic) candidates *before* ranking, so an unrelated prompt injects nothing rather than the least-bad guess. The lexical channel only activates when the dense channel finds the query on-topic, preventing generic words from pulling in noise.

> Tunable via `MEMOR_MIN_SIMILARITY` (relevance floor, default 0.0) and `MEMOR_MAX_TOKENS` (injection budget, default 1500).

## Scoring

Surviving candidates are ranked by four signals:

| Signal | Weight | How it works |
|---|---|---|
| **Semantic similarity** | 50% | Dense + lexical relevance, fused via RRF |
| **Recency** | 25% | Exponential decay with 14-day half-life — recent decisions rank higher |
| **Kind weight** | 15% | Distilled memories (1.3x) rank above raw session chunks (1.0x) |
| **Quality** | 10% | Bayesian score from implicit feedback, bounded to `[0, 1]` — memories the agent actually uses rank higher |

This means a relevant decision from yesterday beats a vaguely-related chunk from a month ago — even if the raw embedding similarity is similar.

Every term is normalized to `[0, 1]` and the weights sum to 1.0, so no single signal can outweigh the rest. That matters more than it sounds: quality is derived from counters, and if those counters go wrong an unbounded quality term stops being a tie-breaker and silently becomes the entire ranking. Scores are clamped on write, on read, and again at the point of use, and counts that violate their own invariant — an artifact used more often than it was recalled — fall back to the neutral prior instead of producing a number from corrupt input.

### Feedback Loop

Memor tracks whether recalled memories actually get used by the agent — and whether they actively hurt. After each session, the daemon analyzes the transcript in both directions:

- **Positive signal** — n-gram overlap or semantic similarity between recalled content and the agent's response. Memories that consistently prove useful get quality boosts.
- **Negative signal** — user rejection ("no that's wrong", "we switched to X") or assistant contradiction ("however, looking at the current code, we actually use Y"). Memories that get corrected receive a quality penalty, making them less likely to be recalled next time.

The quality formula is Bayesian: `(uses - negatives + 1) / (recalls + 2)`, clamped to `[0, 1]`. One correction weighs as much as one positive use, so harmful memories drop fast. Memories never recalled in 30+ days get automatically deactivated. Near-duplicate memories are compacted into one.

> **Known limitation.** The analyzer currently over-counts in both directions: it attributes usage by time window rather than per recall, so on a long-running session an artifact can accrue more uses than it had recalls, and one rejection phrase anywhere in a session penalizes every artifact recalled in it. Quality scores derived from such counts fall back to the neutral prior, so ranking is unaffected — but the per-memory `used` / `rejected` figures are not yet trustworthy, and the dashboard hides that table until they are.

---

## What Gets Stored

| Kind | Source | Description |
|---|---|---|
| `session_chunk` | Daemon auto-ingest | Filtered turns from Claude / Kimi / Goose sessions |
| `memory` | Extractive distillation | Key decisions, patterns, bugfixes per session |

Memories are automatically classified as `decision`, `bugfix`, `lesson`, `snippet`, or generic `extract` based on content patterns. The daemon runs a signal filter that keeps decisions, bugfixes, lessons, and code rationale while skipping noise (tool calls, file listings, boilerplate).

---

## Global Memories

Some patterns aren't project-specific — they're yours. "Always use type hints." "Structure FastAPI apps with a `routes/` directory." "Prefer composition over inheritance."

Memor detects these automatically. When the same pattern appears in **3 or more projects** (measured by embedding similarity), the daemon promotes it to a `_global` scope:

- **Global memories are recalled everywhere** — they show up in every project's search results alongside project-specific memories.
- **Source duplicates are deactivated** — the per-project copies get superseded by the single global version, reducing clutter.
- **No manual tagging** — promotion is fully automatic, based on cross-project clustering.

This means your coding habits and preferences follow you into new projects from the first prompt, without you having to re-explain anything.

---

## Dashboard

```bash
memor dashboard
```

Trading-desk style UI with an **Overview** plus per-agent panes (Claude, Cursor, Codex, Copilot, Kimi, Goose):

- **Overview** — status chips (proxy / hook / daemon, plus which agents are actually *reading* memory and how long any has been silent), portfolio KPIs, cumulative tokens-saved equity curve, recall activity, efficiency, projects, quality, recent recalls
- **Agent desks** — click a tab (or a desk tile) for that environment’s hit rate, latency, proxy savings %, recall volume chart, savings curve, and filtered recalls
- **Proxy savings by agent** — every agent routed through the proxy

---

## Local distillation (optional, no API key)

memor can distill sessions with a small **local** model (offline, in-process,
ingest-only — recall never uses an LLM). Enable it:

```bash
pip install "memor-cli[llm]"   # or: pip install "llama-cpp-python>=0.3.0"
export MEMOR_LLM_DISTILL=1
memor daemon
```

On first run it downloads ~1.1 GB (Qwen2.5-1.5B GGUF, Apache-2.0), cached
thereafter. On CPUs without AVX2, or if the wheel can't build, install a
prebuilt CPU wheel:

```bash
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

If the model is unavailable, distillation falls back to extractive mode
automatically.

---

## Commands

```
memor help                           Print the full manual
memor install-hook                   Install hook + download model (interactive agent picker)
  --agent claude|codex|copilot|kimi|goose   Choose agent directly
memor install-proxy                  Install local proxy for token savings
  --agent claude|codex|goose|kimi|cursor|cline|opencode
memor uninstall-proxy                Restore original agent config
memor proxy                          Run proxy server in foreground (localhost:8421)
memor install-cursor-compress-hooks  Cursor Shell output compression (subscription)
memor uninstall-cursor-compress-hooks
memor install-compress-hook          Compress Bash output before it enters the
                                     transcript (Claude Code); no proxy needed
memor uninstall-compress-hook        Remove it
memor compression-worth              Realized savings, coverage, and net of cache
memor hook-worth                     What tool-output compression saved on your
                                     own Bash output, with its denominator
  --sessions N                       How many recent sessions to scan (default 60)
memor recall-worth                   Whether recall is earning its context budget
memor grade-recalls                  Settle verdicts on recalls the daemon passed
                                     over, so cross-tool memory becomes measurable
memor compress-older                 Show or set whether payloads the agent has
                                     moved past get compressed by the proxy
memor cost-compare                   Did a change actually lower the bill?
memor daemon                         Auto-ingest + distill (Claude, Codex, Cursor, Kimi, Goose, jcode)
memor backfill                       One-shot ingest of past local agent sessions
memor doctor                         Is each wired agent still reading memory?
                                     Names any that silently stopped, and hides
                                     hit rates that predate a behaviour change
memor dashboard                      Web dashboard on localhost:8420
memor version                        Print installed version
memor service install                Run daemon + dashboard as background services (launchd/systemd)
  --no-dashboard                     Install only the daemon
memor service restart                Restart services (keeps proxy if opted in; use after `pipx upgrade`)
memor service stop                   Stop services (warns if agents still point at the proxy)
memor service uninstall              Remove services + restore proxy agent API configs
memor service status                 Show daemon + dashboard (+ proxy) status
  (dashboard port: set MEMOR_DASHBOARD_PORT, default 8420)
memor query <text>                   Search memories from the CLI
memor reingest                       Wipe DB and re-ingest everything
memor reingest --project <name>      Re-ingest only one project
memor forget-stale                   Deactivate memories unused for 30+ days
memor prune                          Retire harness noise and duplicates from the
                                     retrieval pool (deactivates, never deletes)
memor compact                        Rebuild vector index, reclaim wasted space
memor scan                           Audit DB for leaked secrets
memor scan --purge                   Redact secrets in place
memor setup-model                    Download/retry the embedding model
memor ingest-cc <file>               Ingest a single transcript
memor ingest-project <dir>           Bulk ingest a project directory
memor docs watch <dir>               Keep a folder of notes ingested
memor docs list                      Watched folders + notes in the store
memor ingest-doc <file>              One-shot import of a single document
memor distill --project <name>       Run distillation manually
memor eval <cases.json>              Run eval suite
memor eval-counterfactual --project  Win/tie/loss vs no-memory baseline
memor eval-longmemeval               Retrieval accuracy on LongMemEval (ground truth)
memor request-anatomy                Where a request's tokens go, and the ceiling
memor eval-retention                 Does compression keep what the agent used?
memor bench-embed --project <name>   Compare embedding models
```

---

## Architecture

```
memor/
├── types.py / interfaces.py    Core types + Embedder/LLM/MemoryStore protocols
├── cli.py                      Typer CLI (hooks, proxy, daemon, eval, service)
├── daemon.py                   Multi-agent ingest + distill + feedback
├── recall.py                   Shared recall core (hook + skill + proxy inject)
├── service.py                  launchd/systemd: daemon + dashboard (+ proxy)
├── redact.py                   Secret redaction at ingest
├── feedback.py                 Did a served memory get used? Per-agent readers
├── crosstool.py                Reader × writer: did memory cross tools?
├── liveness.py                 Is each agent still reading? Windows stats
│                                 against the last behaviour change
├── backfill_feedback.py        Grade recalls the daemon already passed over
├── global_memories.py          Cross-project promotion to _global scope
│
├── ingest/                     Passive capture — no agent cooperation needed
│   ├── claude_code.py            ~/.claude/projects/ JSONL
│   ├── codex.py                  ~/.codex/sessions/ rollout JSONL
│   ├── cursor.py                 Cursor composer store (SQLite, read-only)
│   ├── jcode.py                  ~/.jcode/sessions/ + journal appends
│   ├── goose.py                  Goose sessions.db (SQLite)
│   ├── kimi.py                   ~/.kimi/sessions/ wire.jsonl
│   ├── documents.py              Markdown / text parsing
│   ├── document_watch.py         Watched note folders (auto-ingested)
│   └── sources.py                Registry used by daemon + backfill
│
├── hook_cli.py                 Hook entry point
├── hook_server.py              Agent detect + response format
│                                 (Claude, Cursor, Codex, Copilot, Kimi,
│                                  Goose, jcode)
│
├── proxy/                      Opt-in token-savings path (localhost:8421)
│   ├── server.py                 Request routing + health
│   ├── pipeline.py               Compress → forward → ledger
│   ├── shim.py                   Fail-open wrapper around the pipeline
│   ├── memory.py                 Recall injection on the proxy path
│   ├── install.py                Wire agent config + backups
│   └── mcp_retrieve.py           memor_recall + memor_retrieve (read-only)
│
├── compress/                   Structure-preserving crushers
│   ├── detect.py                 Content-type classifier + source guard
│   ├── code.py / code_ts.py      AST / tree-sitter skeletonizers
│   └── diff.py, logs.py, search.py, json_crush.py, text.py
│
├── retrieve/retriever.py       Hybrid dense + BM25 (RRF) + relevance gate
├── store/sqlite_store.py       SQLite + sqlite-vec + FTS5 + proxy_savings
│
├── dashboard/                  FastAPI + static UI (savings, cross-tool, agents)
├── distill/                    Extractive default; optional local GGUF LLM
├── embed/                      model2vec local (default) + API/fake
├── eval/                       Counterfactual, proxy benchmarks, baselines
└── llm/                        Anthropic / OpenAI-compat / llama.cpp backends

skill/recall.py                 Standalone recall script
```

---

## Security

**Nothing leaves your machine.** In the default configuration (hooks only, no proxy):

- **No telemetry, no analytics, no phone-home.** Memor makes no outbound network
  calls. The only HTTP it speaks by default is to `127.0.0.1` — health checks
  against its own daemon, dashboard and proxy.
- **Embeddings run locally** via model2vec static token embeddings — no inference runtime, no GPU (one-time model download from HuggingFace — no user data sent).
- **Memory capture is read-only.** The daemon reads agent transcripts off disk;
  it never writes to another tool's files, and its MCP surface exposes recall
  and retrieve only, with no write tool.
- **Hook transport is a Unix socket** (`~/.memor/hook.sock`), not a network port.
- **Dashboard binds localhost only** (`127.0.0.1:8420`).

**With the proxy enabled, Memor is on the wire.** `memor install-proxy` puts Memor in the path of every request your agent makes to Anthropic or OpenAI:

- **The proxy binds localhost only** (`127.0.0.1:8421`) and accepts no remote connections.
- **It makes the outbound call your agent would have made anyway**, to the same provider endpoint, carrying your existing provider API key. Keys are forwarded, never stored or logged.
- **It rewrites request bodies** — compressing tool payloads and appending recalled memories to the latest user message — so what the provider receives is not byte-identical to what your agent sent. Originals stay local in the CCR store.

### Why no Composer interception

Memor does **not** MITM Cursor's subscription traffic. An earlier attempt was measured and
abandoned: with a local proxy in Cursor's path covering both its Node and Chromium network
stacks, only control-plane traffic (telemetry, dashboard, model lists) appeared — no
conversation RPC. And the exchange that actually gets billed, Cursor's servers to the model,
never touches your machine at all, so any local savings figure would be unverifiable.

Compression for Cursor therefore happens where it can be measured honestly: the Shell
compress hooks crush tool output *before* Cursor ingests it. No CA trust, no TLS
interception, nothing to break when Cursor updates.

The only other optional network paths are the LLM-based abstractive distiller (requires explicitly setting `ANTHROPIC_API_KEY`) and the API embedding backend — both off by default.

### Secret redaction

Memor automatically redacts secrets **at ingest**, before anything is embedded or stored:

- API keys (AWS `AKIA...`, OpenAI `sk-...`, Anthropic `sk-ant-...`, GitHub `ghp_...`, Stripe, Slack)
- JWTs, PEM private key blocks
- Connection strings (`postgres://`, `mongodb://`, `redis://`, etc.)
- `.env`-style assignments (`DB_PASSWORD=...`, `API_KEY=...`)
- High-entropy tokens (Shannon entropy > 4.0, length > 20)

Redacted content is replaced with `[REDACTED]` in place, preserving surrounding context. To audit and clean an existing database: `memor scan` (audit) or `memor scan --purge` (redact in place).

### Contradiction handling

When a new memory contradicts an older one in the same project (detected via replacement cues like "switched from X to Y", "no longer", "ripped out"), the older memory is automatically deactivated. This prevents stale decisions from being recalled and misleading the agent.

### Local storage

The memory database (`~/.memor/memor.db`) is stored as plaintext SQLite on disk. For at-rest protection, we recommend enabling OS-level full-disk encryption (FileVault on macOS, LUKS on Linux) which covers all local files with zero performance overhead.

---

## Development

```bash
git clone https://github.com/bnimit/memor-ai.git
cd memor-ai
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest  # 1,760+ tests
```

---

## License

MIT. See [LICENSE](LICENSE) for the full text.
