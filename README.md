```
                                                _
 _ __ ___   ___ _ __ ___   ___  _ __       __ _(_)
| '_ ` _ \ / _ \ '_ ` _ \ / _ \| '__|____ / _` | |
| | | | | |  __/ | | | | | (_) | | |_____| (_| | |
|_| |_| |_|\___|_| |_| |_|\___/|_|        \__,_|_|

  Measured memory and opt-in token savings for coding agents.
```

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-445%20passing-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)]()
[![PyPI](https://img.shields.io/pypi/v/memor-cli.svg)](https://pypi.org/project/memor-cli/)

**Automatic background memory for Claude Code, Cursor, Codex, Copilot, Kimi, and Goose — plus optional local token savings for Claude Code and Codex.** Memory is fire-and-forget; proxy is opt-in. No Memor API key required.

Memor watches your coding sessions, extracts decisions and patterns, and recalls relevant context on every prompt. Optionally, a local proxy compresses tool payloads before they reach the provider and tracks measurable token savings on the dashboard.

---

## Quick Start

```bash
# Install globally (recommended)
pipx install memor-cli

# Install the hook + download embedding model (~60MB)
memor install-hook                  # interactive — pick an agent
memor install-hook --agent kimi     # or pass directly (claude, codex, copilot, kimi, goose)

# Start as a background service (macOS/Linux)
memor service install

# Or run in the foreground
memor daemon
```

That's it. Every prompt now gets automatic context recall. `memor service install` also starts the dashboard as a background service, so it's already live at http://localhost:8420 (and is recycled whenever you stop/restart/uninstall the service). To run it in the foreground instead:

```bash
memor dashboard
# Opens http://localhost:8420
```

> **Alternative install:** `pip install memor-cli` works too — just make sure `~/.local/bin` is on your PATH so the `memor` command is available.

### Optional: Token savings proxy

Memory works out of the box via hooks. To also compress tool payloads and track token savings:

```bash
memor install-proxy --agent claude   # or: codex, goose, kimi, cursor, cline, opencode
```

This points your agent at a local proxy on `127.0.0.1:8421`, compresses latest-turn tool content before it reaches the provider, and logs savings to the dashboard. For proxied agents, hooks skip inject (memory comes from the proxy path); Cursor and Copilot always use hooks only.

`memor service restart` (e.g. after `pipx upgrade`) keeps the proxy running when it was opted in. If the proxy fails its health check on install, Memor restores your agent’s original API URLs so calls are not left pointing at a dead localhost port. `memor service stop` warns while agents still point at the proxy; `memor service uninstall` restores direct API configs for proxy-enabled agents.

To revert: `memor uninstall-proxy --agent claude`

> **Codex support is experimental.** The proxy implements the OpenAI Chat Completions API (`/v1/chat/completions`). Codex CLI may instead use the Responses API (`/v1/responses`) depending on version and model, in which case requests will not route through the proxy and you will see no savings. Memory via hooks is unaffected. Track it in [#26](https://github.com/bnimit/memor-ai/issues/26).

### Optional: Full Cursor install (recommended)

**Memory alone** still only needs `memor install-hook` (Claude covers Cursor). For token savings on Cursor, one command enables the full stack:

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
       (memory, all agents)          (savings, opt-in)             SQLite + vec
              │                             │                      + FTS + ledger
              │ recall inject               │ compress latest-turn
              │ always on after             │ tool payloads; forward
              │ install-hook                │ to provider; CCR originals
              ▼                             ▼
       Claude · Cursor · Codex         Anthropic / OpenAI
       Copilot · Kimi · Goose          (your existing credentials)
              ▲
              │
       Daemon ingests sessions ── Claude ~/.claude/projects/
                                  Kimi   ~/.kimi/sessions/
                                  Goose  ~/.local/share/goose/...
```

| Path | Purpose | Default |
|------|---------|---------|
| **Hooks** | Shared memory recall across all agents | On after `memor install-hook` |
| **Proxy** | Compress tool payloads; ledger token savings | Opt-in via `memor install-proxy` |

**Memory is fire-and-forget** — install hooks once, every prompt gets relevant context. **Proxy is opt-in** — for Claude Code, Codex, Goose, and Kimi when you want measurable token savings. Proxied agents skip hook inject (memory comes from the proxy path); Cursor and Copilot always use hooks. The proxy forwards your existing Anthropic/OpenAI credentials; Memor does not require its own API key.

---

## How It Works (hooks path)

```
  You type a prompt
  (Claude · Cursor · Codex · Copilot · Kimi · Goose)
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
| **Cursor** | Yes | BYOK proxy + Shell compress hooks |
| **Copilot CLI** | Yes | No — hooks only |
| **Kimi CLI** | Yes | Yes — `memor install-proxy --agent kimi` |
| **Goose** | Yes | Yes — `memor install-proxy --agent goose` (auto-detects common Desktop custom providers like `custom_deepseek`; use `--upstream-url` if yours is custom) |
| **Cline** | No | Yes — `memor install-proxy --agent cline` |
| **OpenCode** | No | Yes — `memor install-proxy --agent opencode` |

### Hook install details

| Agent | Hook protocol | Config location | Install |
|---|---|---|---|
| **Claude Code** | `UserPromptSubmit` + `additionalContext` | `~/.claude/settings.json` | `memor install-hook --agent claude` |
| **Codex CLI** | `UserPromptSubmit` + `additionalContext` | `~/.codex/hooks/hooks.json` | `memor install-hook --agent codex` |
| **Copilot CLI** | `userPromptSubmitted` + `additionalContext` | `~/.copilot/hooks/memor.json` | `memor install-hook --agent copilot` |
| **Cursor** | `beforeSubmitPrompt` + `additionalContext` | `~/.claude/settings.json` (loaded as Claude user hooks) | automatic — covered by the Claude install |
| **Kimi CLI** | `UserPromptSubmit` + plain-text context | `~/.kimi/config.toml` | `memor install-hook --agent kimi` |
| **Goose** | `UserPromptSubmit` + `additionalContext` | `~/.agents/plugins/memor/` | `memor install-hook --agent goose` |

A single `memor-hook` binary auto-detects which agent is calling it — no separate entry points needed. Kimi and Goose installs stamp `MEMOR_HOOK_AGENT` so Claude-shaped payloads stay correctly labeled. Cursor loads the same Claude user hooks, so installing for Claude Code covers Cursor too. When an agent is proxied, its hook skips inject and memory comes from the proxy path; Cursor and Copilot always inject via hooks. The dashboard tracks recalls per agent so you can see usage across all your environments.

> **Goose note:** Memory inject needs a Goose build with advise-tier `additionalContext` support. DeepSeek (or any other provider) is configured inside Goose — Memor talks to Goose's hooks, not to the model provider.

> **Note:** Cloud-hosted agents (Codex cloud, Copilot cloud agent) run in remote sandboxes and cannot reach local hooks. MCP server support for sandboxed agents is planned ([#26](https://github.com/bnimit/memor-ai/issues/26)).

**Background processes** (supervised by `memor service install`):

1. **Daemon** — polls local agent session stores (Claude Code `~/.claude/projects/`, Kimi `~/.kimi/sessions/`, Goose `~/.local/share/goose/sessions/sessions.db`), embeds chunks, runs distillation, analyzes feedback (Claude), promotes cross-project patterns to global scope, compacts duplicates, auto-compacts the vector index when bloated, tracks session-level token usage. Model providers are not ingest sources — only the agent that owns the session. All local. Use `memor backfill` for a one-shot ingest of past sessions.
2. **Hook** — fires on every prompt, recalls relevant memories, injects them as context. Sub-15ms. Works across Claude Code, Cursor, Codex, Copilot, Kimi, and Goose.
3. **Proxy** (optional) — intercepts Anthropic/OpenAI API calls on `127.0.0.1:8421`, compresses latest-turn tool payloads, forwards to your provider, and writes a savings ledger. Started automatically by `memor install-proxy`.

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
| **Quality** | 10% | Bayesian score from implicit feedback — memories the agent actually uses rank higher |

This means a relevant decision from yesterday beats a vaguely-related chunk from a month ago — even if the raw embedding similarity is similar.

### Feedback Loop

Memor tracks whether recalled memories actually get used by the agent — and whether they actively hurt. After each session, the daemon analyzes the transcript in both directions:

- **Positive signal** — n-gram overlap or semantic similarity between recalled content and the agent's response. Memories that consistently prove useful get quality boosts.
- **Negative signal** — user rejection ("no that's wrong", "we switched to X") or assistant contradiction ("however, looking at the current code, we actually use Y"). Memories that get corrected receive a quality penalty, making them less likely to be recalled next time.

The quality formula is Bayesian: `(uses - negatives + 1) / (recalls + 2)`. One correction weighs as much as one positive use, so harmful memories drop fast. Memories never recalled in 30+ days get automatically deactivated. Near-duplicate memories are compacted into one.

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

- **Overview** — status chips (proxy / hook / daemon), portfolio KPIs, cumulative tokens-saved equity curve, recall activity, efficiency, projects, quality, recent recalls
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
memor daemon                         Auto-ingest + distill (Claude, Kimi, Goose)
memor backfill                       One-shot ingest of past local agent sessions
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
memor compact                        Rebuild vector index, reclaim wasted space
memor scan                           Audit DB for leaked secrets
memor scan --purge                   Redact secrets in place
memor setup-model                    Download/retry the embedding model
memor ingest-cc <file>               Ingest a single transcript
memor ingest-project <dir>           Bulk ingest a project directory
memor ingest-doc <file>              Ingest a markdown document
memor distill --project <name>       Run distillation manually
memor eval <cases.json>              Run eval suite
memor eval-counterfactual --project  Win/tie/loss vs no-memory baseline
memor bench-embed --project <name>   Compare embedding models
```

---

## Architecture

```
memor/
+-- types.py / interfaces.py   Core types + Embedder/LLM/MemoryStore protocols
+-- cli.py                     Typer CLI (hooks, proxy, daemon, eval, service)
+-- daemon.py                  Multi-agent ingest + distill + compaction
+-- recall.py                  Shared recall core (hook + skill + proxy inject)
+-- service.py                 launchd/systemd: daemon + dashboard (+ proxy)
+-- redact.py / feedback.py    Secret redaction; positive/negative quality loop
+-- global_memories.py         Cross-project promotion to _global scope
|
+-- ingest/
|   +-- claude_code.py         ~/.claude/projects/ JSONL
|   +-- kimi.py                ~/.kimi/sessions/ wire.jsonl
|   +-- goose.py               Goose sessions.db
|   +-- sources.py             Registry used by daemon + backfill
|
+-- hook_cli.py / hook_server.py
|                              Hook entry + agent detect/format
|                              (Claude, Cursor, Codex, Copilot, Kimi, Goose)
|
+-- proxy/                     Opt-in token-savings path (localhost:8421)
|   +-- server.py / pipeline.py  Compress → forward → ledger
|   +-- install.py               Wire agent config + backups
|   +-- mcp_retrieve.py          memor_retrieve MCP tool
|
+-- compress/                  Log / search / JSON crushers for tool payloads
|
+-- retrieve/retriever.py      Hybrid dense + BM25 (RRF) + relevance gate
+-- store/sqlite_store.py      SQLite + sqlite-vec + FTS5 + proxy_savings
|
+-- dashboard/                 FastAPI + static UI (status, savings, agents)
+-- distill/                   Extractive default; optional local GGUF LLM
+-- embed/                     model2vec local (default) + API/fake
+-- eval/                      Counterfactual, proxy benchmark fixtures, baselines
+-- llm/                       Anthropic / OpenAI-compat / llama.cpp backends

skill/recall.py                Standalone recall script
```

---

## Security

**Nothing leaves your machine.** In the default configuration (hooks only, no proxy):

- **No telemetry, no analytics, no phone-home.** Memor itself makes zero outbound network calls.
- **Embeddings run locally** via model2vec static token embeddings — no inference runtime, no GPU (one-time model download from HuggingFace — no user data sent).
- **Hook transport is a Unix socket** (`~/.memor/hook.sock`), not a network port.
- **Dashboard binds localhost only** (`127.0.0.1:8420`).

**With the proxy enabled, Memor is on the wire.** `memor install-proxy` puts Memor in the path of every request your agent makes to Anthropic or OpenAI:

- **The proxy binds localhost only** (`127.0.0.1:8421`) and accepts no remote connections.
- **It makes the outbound call your agent would have made anyway**, to the same provider endpoint, carrying your existing provider API key. Keys are forwarded, never stored or logged.
- **It rewrites request bodies** — compressing latest-turn tool payloads — so what the provider receives is not byte-identical to what your agent sent. Originals stay local in the CCR store.

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

pytest  # 445 tests
```

---

## License

MIT. See [LICENSE](LICENSE) for the full text.
