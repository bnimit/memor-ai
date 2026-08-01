# Dual-Path Context Layer — Design Spec

**Date:** 2026-08-01  
**Status:** Draft for user review (approved in brainstorming)  
**Product:** memor-cli / Memor  
**Goal:** One local product that (1) measurably reduces billed tokens via a Headroom-style proxy and (2) keeps shared cross-agent memory via hooks — with savings as the primary KPI.

---

## 1. Problem

Memor is a strong **additive** memory layer (hybrid retrieval, relevance gate, multi-agent hooks, feedback loop). Write-side and recall-side experiments hit a practical ceiling (~65% retrieval coverage, ~96.6% do-no-harm on the coding corpus). Further distillation A/B work does not clear the noise floor.

The original vision was shared contextual memory that **reduces token usage and improves ROI**. Today Memor mostly *adds* context and hopes a recalled fact saves turns. Competitors like Headroom deliver **tangible** ROI by *subtracting* tokens on the path to the model (compress tool outputs, logs, bulky payloads) with a receipt users can see.

**Gap:** Memor has no request-path compression, no provider-protocol intercept, and a dashboard that cannot show Headroom-like before/after savings for live agent traffic.

---

## 2. Goal & non-goals

### Goal

Ship a **local dual-path context layer**:

| Path | Purpose | Agents (v1) |
|------|---------|-------------|
| **Proxy (opt-in)** | Compress request-side tool payloads; forward to the user’s existing provider; ledger savings | Claude Code, Codex CLI |
| **Hooks (default)** | Shared memory inject across agents | Claude Code, Cursor, Codex, Copilot |

Primary KPI: **token savings** (visible on dashboard).  
Secondary KPI: **cross-agent memory continuity** (existing recall/quality metrics).

### Non-goals (v1)

- Memor-owned cloud LLM or second-model compression (LLMLingua, etc.)
- Cloud/remote agents (Codex cloud, Copilot cloud) — MCP for sandboxes remains later (#26)
- Desktop/menu-bar app
- Aggressive full-history rewriting / “IntelligentContext” pruning
- Deprecating hooks
- Binding the proxy beyond localhost

---

## 3. Constraints (non-negotiable)

1. **No Memor-owned internet dependency.** Compressors run locally. Proxy only forwards the agent’s existing Anthropic/OpenAI calls. Memor does not require its own API key.
2. **Memory stays fire-and-forget.** Proxy is **one explicit opt-in** (`memor install-proxy`).
3. **Hooks stay** for all supported agents; proxy does not replace multi-agent coverage.
4. **Hooks stay in charge of memory.** Proxy-side inject is best-effort (no reliable cwd → weak project scope). Hooks do **not** skip when an agent is proxied; accept a possible double-inject rather than zero memory. Revisit skip only once proxy inject can scope by project.
5. **Stream passthrough.** Responses stream through unchanged. Compression is **request-side only**.
6. **Cache-aware.** Do not rewrite stable message prefixes. v1 compresses **latest-turn tool payloads / oversized tool messages only**.
7. **Keys never persisted.** Forward `Authorization` / provider headers; do not store API keys.
8. **Localhost only.** Bind `127.0.0.1`; refuse `0.0.0.0` in v1.
9. **Stop chasing memory win-rate.** Distillation counterfactual ceiling stands; invest in compression + measurable ROI.

---

## 4. Architecture

```
                    ┌─────────────────────────────────────┐
                    │     Memor local service (one unit)  │
                    │  daemon + hook + proxy + dashboard  │
                    └──────────────┬──────────────────────┘
                                   │
           ┌───────────────────────┼───────────────────────┐
           ▼                       ▼                       ▼
    Path A: Hooks            Path B: Proxy            Shared store
    (all agents)             (Claude Code, Codex)     SQLite + vec
           │                       │                  + savings ledger
           │ memory inject         │ compress + forward + CCR originals
           │                       │ + memory once
           ▼                       ▼
    Claude / Cursor /         Anthropic / OpenAI
    Codex / Copilot           (user's existing providers)
```
**Process model:** One `memor service` supervises daemon + dashboard + proxy. Status/dashboard shows health of each.

**Default ports (localhost only):** dashboard `127.0.0.1:8420` (existing); proxy `127.0.0.1:8421`.

---

## 5. Components

### Reuse

| Component | Role |
|-----------|------|
| Daemon | Ingest, distill, feedback, global promotion, compaction |
| Hook server | Prompt-time recall for all supported agents |
| Retriever + SQLite store | Hybrid search, quality, existing ROI tables |
| Service manager | Extend to supervise proxy |

### New

| Component | Path | Role |
|-----------|------|------|
| Proxy | `memor/proxy/` | Anthropic Messages + OpenAI Chat Completions forwarder; compress; ledger; optional memory inject |
| Protocol adapters | `memor/proxy/adapters/` | Normalize provider message shapes → shared compress pipeline |
| Compressors | `memor/compress/` | Local content-typed compressors (logs, JSON, search dumps, plain text) |
| CCR store | SQLite table in `memor.db` | Compress-Cache-Retrieve originals for `memor_retrieve` |
| MCP retrieve | MCP server | `memor_retrieve` for proxied agents |
| Proxy install/uninstall | CLI | Point agent at localhost; backup/restore prior config; register MCP |
| Savings ledger | New DB tables | Per-request before/after tokens, content-type breakdown, agent, session |
| Dashboard savings UI | `memor/dashboard/` | Hero savings, dual-path status, compression breakdown |

### v1 agent matrix

| Agent | Memory (hooks) | Proxy / savings |
|-------|----------------|-----------------|
| Claude Code | Yes (hooks always inject; proxy inject is best-effort) | Yes |
| Codex CLI | Yes (hooks always inject; proxy inject is best-effort) | Yes |
| Cursor | Yes | No |
| Copilot CLI | Yes | No |

---

## 6. Data flow

### 6.1 Proxied agent (Claude Code / Codex)

1. User prompt → agent builds request (messages + tool results).
2. Request hits Memor proxy on localhost.
3. Protocol adapter normalizes messages.
4. Compressors run on **latest-turn tool payloads** only (cache-safe).
5. Originals stored in CCR; MCP `memor_retrieve` available.
6. Proxy may inject memories best-effort (same markdown shape as the hook) when a project hint is available; otherwise hooks remain the reliable inject path.
7. Ledger records tokens before/after + content-type breakdown.
8. Forward to Anthropic/OpenAI; **stream** response through.
9. Agent may call `memor_retrieve` if it needs full detail.

Hooks for that agent: **still fire** (do not skip). Proxy inject is supplementary until project scoping is reliable.

### 6.2 Hooks-only agent (Cursor / Copilot)

Unchanged: hook fires → recall → inject if relevant. No compression, no savings ledger for that turn. **Never** skip hooks for Cursor/Copilot even if Claude/Codex are proxied.

### 6.3 Background

Daemon continues ingest → distill → feedback → store. Dashboard reads savings ledger (primary) + recall stats (secondary).

---

## 7. Compression rules (v1)

- **Local only** — no LLM compressor.
- **Content types (initial):** build/test logs, JSON tool dumps, search-result style dumps, oversized plain text.
- **Preserve always** (even without retrieve): error lines, stack traces, file paths, FATAL/severity markers.
- **Safe if MCP unused:** compression must remain useful without retrieve; retrieve is for aggressive reversible detail.
- **Images / multipart:** passthrough uncompressed.
- **Compressor error:** passthrough uncompressed; ledger marks `passthrough`.
- **CCR retention:** TTL **7 days** + **2 GB** default disk cap (configurable); oldest evicted first; post-eviction retrieve → clear miss error.

---

## 8. Install / uninstall / skip wiring

### `memor install-proxy --agent {claude|codex}`

1. Ensure proxy supervised by `memor service`.
2. Backup exact prior base_url / env / config snippet to `~/.memor/proxy-backup-<agent>.json`.
3. Point agent at localhost proxy (Anthropic or OpenAI as appropriate).
4. Register `memor_retrieve` MCP for that agent.
5. Set **per-agent** flag: `proxy_agents.<agent> = true` in Memor config (dashboard/status + future skip once proxy inject is reliable).
6. Health-check proxy; print dashboard URL for savings.

### `memor uninstall-proxy --agent …`

Restore backed-up config; clear per-agent proxy flag; leave daemon/hooks intact.

### Hook / proxy inject semantics (current)

- Hooks always inject when they have a project cwd (Claude/Codex/Cursor/Copilot/Kimi/Goose).
- Proxy may also inject when `x-memor-project` / similar is present; otherwise project=`unknown` and inject is weak.
- Double-inject is an accepted interim tradeoff vs zero memory. Do not skip hooks until proxy project scoping is fixed.

### Fail modes

| Failure | Behavior |
|---------|----------|
| Compressor error | Passthrough; log + ledger `passthrough` |
| Upstream provider error | Forward status/body unchanged; no custom retries |
| Proxy process down | Agent calls fail; dashboard red; user `memor service start` or `uninstall-proxy` |
| CCR miss on retrieve | Clear tool error |

---

## 9. Dashboard

### Primary (new)

- Hero: tokens before vs after, % saved
- Provider `usage` when present (input / cache_read / output)
- Optional $ estimate — **labeled estimate only**
- Dual-path status pills (proxy / hook / daemon)
- Compression breakdown by content type
- Per-agent / recent session savings

### Secondary (existing, demoted visually)

- Recall count, hit rate, latency, quality leaderboard, project breakdown

### KPI definitions

- **Primary:** estimated tokens removed pre-forward, reconciled with provider `usage` when the response includes it.
- **Secondary:** existing recall/quality metrics.
- Do not present RAG “recalled vs full corpus” as billed savings.

---

## 10. Testing & release gate

### Tests

- Unit: compressors (logs/JSON/search), adapters, ledger math, CCR TTL/eviction
- Integration: fake upstream + streaming passthrough
- Hook skip: proxied Claude no double-inject; Cursor still injects when Claude proxied
- Install/uninstall: config backup/restore
- Bind address: refuses non-localhost

### Release gate

Run a fixed suite of **≥5 scripted coding tasks** (at least 3 tool-rich / log-heavy) with/without proxy:

- **Pass criteria:** every task passes the same automated checks as baseline (tests / exit codes). No LLM judge required for v1 ship.
- **Savings criteria:** mean request-side token reduction **≥15%** on the tool-rich subset; no task may regress to failure.
- Do not ship on compressor unit tests alone.

### Pre-ship verification

Confirm daemon transcript ingest still works when agents use localhost `base_url`. If broken, fix before release or document as a blocking bug.

---

## 11. Rollout phases

1. Compressors + savings ledger + dashboard savings UI (flagged; no agent install yet)
2. Anthropic proxy path + Claude Code `install-proxy` + per-agent hook skip
3. OpenAI path + Codex `install-proxy`
4. MCP `memor_retrieve` + CCR TTL/cap
5. Docs/README: memory = FAF; proxy = opt-in savings; dual-path status
6. Benchmark gate → release

---

## 12. Success criteria (v1)

A proxied Claude Code session shows **visible, believable token savings** on the dashboard without breaking the agent; Cursor/Copilot keep shared memory via hooks; uninstall restores prior config cleanly; Memor never requires its own API key.

---

## 13. Relationship to prior work

- Distillation quality research (2026-06-17): ceiling acknowledged; this spec **does not** reopen H1–H6.
- Token savings measurement (2026-06-05): session_stats from transcripts remain useful; proxy ledger is the new primary signal for opt-in proxy users.
- MCP server design (#26 / 2026-06-09): cloud/sandbox MCP stays separate; this spec only adds local `memor_retrieve` for proxied agents.
- Write-side distillation overhaul (2026-07-01): orthogonal; may proceed independently; not required for proxy v1.
