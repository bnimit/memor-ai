# Goose + Kimi Proxy, Dashboard Breakdown & Fail-Open Shim — Design Spec (0.11.0)

**Date:** 2026-08-01  
**Status:** Draft for user review  
**Product:** memor-cli / Memor  
**Release:** 0.11.0  
**Supersedes:** [Proxy Lifecycle design](./2026-08-01-proxy-lifecycle-failover-design.md) decisions **D2** and **D3** (shim + Goose/Kimi were out of scope there; both are in scope here). Lifecycle behavior from #42 (merged) remains; this spec extends it.

**Goal:** Extend the dual-path proxy to **Goose** and **Kimi (CLI / VS Code via CLI)**, add **per-agent proxy savings** on the dashboard, and ship a **runtime fail-open shim** so agents keep working when the compressor is unhealthy — without waiting for config failover.

**References:**
- [Dual-path context layer](./2026-08-01-dual-path-context-layer-design.md)
- [Proxy lifecycle + config failover](./2026-08-01-proxy-lifecycle-failover-design.md) (merged #42)
- [Goose providers](https://goose-docs.ai/docs/getting-started/providers/)
- [Kimi Code VS Code](https://www.kimi.com/code/docs/en/kimi-code-for-vscode/getting-started)

---

## 1. Problem

v1 proxy covers **Claude Code** and **Codex** only. Upstream URLs are **hardcoded** to `api.anthropic.com` and `api.openai.com`, so OpenAI-compatible agents (Goose on DeepSeek/Moonshot, Kimi on `api.kimi.com/coding/v1`) cannot be proxied.

Users running **Goose** (`custom_deepseek`, OpenRouter, etc.) and **Kimi Code for VS Code** (wrapper around Kimi CLI) get **hooks-only** memory — no measurable compression savings on the ledger.

Additional gaps:

| Gap | Impact |
|-----|--------|
| Ledger `agent` defaults to `unknown` without install-time stamping | Cannot attribute savings to Goose/Kimi |
| Dashboard Token Savings is **aggregate**; Agent Breakdown is **recall_log** only | No per-agent compression ROI |
| Runtime proxy/compressor failure | Agent stuck on `:8421` until manual uninstall or config failover (#42 covers **install-time** only) |
| Hook path still injects for proxied agents | Possible double inject; original dual-path spec deferred skip |

**0.12 (explicit parking lot):** Cursor compression, richer crushers (code/diff), multimodal compression.

---

## 2. Scope — 0.11.0 vs 0.12

### In scope (0.11.0)

1. **Goose proxy** — `memor install-proxy --agent goose`
2. **Kimi proxy** — `memor install-proxy --agent kimi` (CLI + VS Code via same CLI config)
3. **Configurable per-agent upstream** — capture at install, restore on uninstall/failover
4. **Agent identity on ledger** — `x-agent: goose|kimi` (and path fallback)
5. **Hook skip when proxied** — with minimal project-scoping fix for proxy inject
6. **Per-agent proxy savings** — dashboard + API
7. **Runtime fail-open shim** — uncompressed passthrough on same port when compressor unhealthy
8. **Proxy lifecycle** — already on main (#42); bundled in 0.11.0 release notes

### Out of scope (0.12)

- **Cursor compression** — no clean local base-URL rewrite today
- **New crushers / multimodal compression** — separate R&D track
- Mid-session background watchdog that rewrites agent configs (config failover stays install/uninstall/stop-time only)

---

## 3. Architecture

```
  Goose / Kimi / Claude / Codex
           │
           │  HTTP (OpenAI and/or Anthropic routes)
           ▼
  ┌────────────────────────────────────────────┐
  │  Memor proxy 127.0.0.1:8421                │
  │  ┌──────────────────────────────────────┐  │
  │  │ Route by x-agent (or /goose/ prefix) │  │
  │  └──────────────────────────────────────┘  │
  │  ┌─────────────┐    ┌──────────────────┐  │
  │  │ Compressor  │───▶│ Forward upstream │  │
  │  │ (pipeline)  │    │ (per-agent URL)  │  │
  │  └─────────────┘    └──────────────────┘  │
  │         │ fail / passthrough flag          │
  │         └──────────────────────────────────│
  │              Fail-open shim (same port)    │
  └────────────────────────────────────────────┘
           │
           ▼
  Captured upstream per agent (DeepSeek, Kimi, Anthropic, …)
```

**Two fail-open layers (both kept):**

| Layer | When | Behavior |
|-------|------|----------|
| **Config failover** (#42) | `install-proxy` health gate fails; `service uninstall` | Restore agent config backups; clear `proxy_agents` flags |
| **Runtime shim** (new) | Compressor error, embedder down, pipeline panic | Forward **uncompressed** to captured upstream; agent keeps working |

Config failover = “get off Memor entirely.” Runtime shim = “Memor still forwards, just without compression.”

---

## 4. Config schema

Extend `~/.memor/config.json`:

```json
{
  "proxy_port": 8421,
  "proxy_agents": {
    "claude": true,
    "goose": true,
    "kimi": true
  },
  "proxy_upstreams": {
    "claude": {
      "protocol": "anthropic",
      "base_url": "https://api.anthropic.com/v1/messages",
      "provider_name": "anthropic"
    },
    "goose": {
      "protocol": "openai",
      "base_url": "https://api.deepseek.com/v1/chat/completions",
      "provider_name": "custom_deepseek"
    },
    "kimi": {
      "protocol": "openai",
      "base_url": "https://api.kimi.com/coding/v1/chat/completions",
      "provider_name": "kimi"
    }
  }
}
```

| Field | Meaning |
|-------|---------|
| `protocol` | `anthropic` or `openai` — selects Memor route handler and adapter |
| `base_url` | **Full** upstream URL for POST (including path) |
| `provider_name` | Display/debug only (Goose custom provider id, Kimi provider key, etc.) |

**Rules:**
- Written at `install-proxy` from captured agent config; never guessed.
- Each agent entry is independent; installing Goose must not overwrite Kimi upstream.
- Re-install with existing backup: backup untouched (same rule as Claude #42).
- Claude/Codex installs populate entries on first 0.11 upgrade when user re-runs install or via migration helper (optional one-time backfill from known defaults if flag set but upstream missing).

---

## 5. Proxy routing & upstream forward

### 5.1 Agent resolution (order)

1. Request header `x-agent` (set at install via Goose custom headers / Kimi `custom_headers` / Claude env — see §6)
2. Request header `agent` (legacy alias)
3. Optional path prefix `/agents/{agent}/v1/...` (fallback only; not required for v1 if headers always set)
4. Default `unknown` — forward using **protocol default** upstream if unmapped (Anthropic route → first anthropic upstream; avoid silent mis-route in tests)

### 5.2 Upstream URL selection

Replace hardcoded URLs in `memor/proxy/server.py`:

```python
upstream_url = resolve_upstream(agent, protocol)  # from proxy_upstreams[agent].base_url
```

If `agent` is unknown or missing upstream entry: **passthrough shim behavior** — return 502 with JSON error unless legacy single-provider default applies for Claude-only installs.

### 5.3 Auth & headers

- Forward client `Authorization` and provider-specific headers unchanged (`sanitize_request_headers` must not drop auth).
- Do **not** persist API keys in Memor config.
- Goose keychain-backed keys remain on the client; Goose sends them on each request.

### 5.4 Ledger

Continue `record_proxy_savings` with:
- `agent` from resolution above
- `provider` = API protocol (`anthropic` / `openai`) for adapter compatibility
- Optional future column or JSON field for `provider_name` / upstream host (v1: store in existing row via extending `content_types` or add `upstream_host` column if migration is cheap — **prefer** logging `provider_name` in a new nullable `upstream_label` text column)

**Passthrough / shim rows:** `passthrough=1`; dashboard `% saved` excludes or segments these so shim traffic does not inflate savings.

### 5.5 Health endpoint (extended)

```json
{
  "ok": true,
  "bind": "127.0.0.1",
  "mode": "compress",
  "compressor_ready": true
}
```

When compressor unavailable but shim active:

```json
{
  "ok": true,
  "mode": "passthrough",
  "compressor_ready": false
}
```

**Install-time health gate (#42):** still requires `ok: true` (shim counts as healthy — port serves traffic).  
**Dashboard:** show proxy mode (`compress` vs `passthrough`).

---

## 6. Install — Goose

**Command:** `memor install-proxy --agent goose`

### 6.1 Discovery

Read `~/.config/goose/config.yaml` → `active_provider`.

Resolve upstream by provider kind:

| Provider kind | Config source | Capture |
|---------------|---------------|---------|
| Custom (`custom_*`) | `~/.config/goose/custom_providers/{name}.json` | `base_url`, `engine` → `protocol` |
| Built-in OpenAI | env / goose config: `OPENAI_HOST`, `OPENAI_BASE_PATH` | `{host}/{base_path}` |
| Built-in Anthropic | `ANTHROPIC_HOST` | `{host}/v1/messages` |

**Desktop-created providers:** JSON should live under `custom_providers/` per [Goose docs](https://goose-docs.ai/docs/getting-started/providers/#configure-custom-provider). If `active_provider` is set but JSON is missing (observed: `custom_deepseek` with no file):

- **Fail install** with actionable message: open Goose → Settings → Models → re-save provider, or create JSON manually, then re-run `install-proxy`.

### 6.2 Rewrite

1. `backup_agent_config("goose")` → `~/.memor/proxy-backup-goose.yaml` (+ separate backup for touched JSON if custom provider)
2. Point Goose at Memor:
   - Custom JSON: set `base_url` to `http://127.0.0.1:{port}/v1/chat/completions` (openai engine) or `http://127.0.0.1:{port}/v1/messages` (anthropic engine)
   - OpenAI built-in: `OPENAI_HOST=http://127.0.0.1:{port}`, `OPENAI_BASE_PATH=v1/chat/completions`
   - Anthropic built-in: `ANTHROPIC_HOST=http://127.0.0.1:{port}`
3. Add stamping header:
   - Custom JSON: `"headers": { "x-agent": "goose", ...existing }`
   - OpenAI built-in: `OPENAI_CUSTOM_HEADERS` or equivalent env goose reads
4. Write `proxy_upstreams.goose` in Memor config
5. `set_proxy_agent("goose", True)`
6. Register `memor_retrieve` MCP if Goose MCP config surface exists (same pattern as Claude — best-effort, non-fatal if path unknown)
7. Health gate → config failover on failure (#42)

### 6.3 Uninstall / failover

- Restore YAML + custom provider JSON from backup
- Strip Memor localhost URLs if backup missing (mirror `_strip_memor_proxy_urls`)
- Clear `proxy_agents.goose` and `proxy_upstreams.goose`

---

## 7. Install — Kimi

**Command:** `memor install-proxy --agent kimi`

### 7.1 Discovery

Primary: `~/.kimi/config.toml` (same path as `memor install-hook`; fallback `~/.kimi-code/config.toml`).

Read active provider section:
- `type` → `kimi` | `openai` | `anthropic` | …
- `base_url` → capture full upstream root or completions URL per Kimi docs
- `custom_headers` if present

**VS Code:** extension passes env via `kimi.environmentVariables` but CLI `config.toml` is source of truth when CLI is installed (`kimi.executablePath`). Document that API-key / configurable provider mode is required; pure account-login with no writable `base_url` → **fail install** with link to Kimi VS Code configuration docs.

**Protocol mapping:**

| Kimi provider `type` | Memor route | Example captured URL |
|---------------------|-------------|----------------------|
| `openai`, `kimi` (OpenAI-compat) | `/v1/chat/completions` | `https://api.kimi.com/coding/v1/chat/completions` |
| `anthropic` | `/v1/messages` | `https://api.kimi.com/coding/v1/messages` |

### 7.2 Rewrite

1. Backup `config.toml` → `~/.memor/proxy-backup-kimi.toml`
2. Set provider `base_url` to `http://127.0.0.1:{port}/v1` (OpenAI) or host root for Anthropic per Kimi’s URL rules
3. Merge `custom_headers.x-agent = "kimi"`
4. Write `proxy_upstreams.kimi`
5. `set_proxy_agent("kimi", True)`
6. Register MCP in Kimi config if section exists (best-effort)
7. Health gate

### 7.3 Uninstall / failover

Same pattern as Goose; TOML-aware strip for Memor localhost `base_url`.

---

## 8. Install — Claude / Codex (changes)

Existing flows remain; extend to:

1. Populate `proxy_upstreams.claude` / `.codex` on install (capture pre-proxy URL from backup snapshot)
2. Extend `failover_proxy_agents` registry — replace hardcoded `_agent_paths()` agent list with registry table:

```python
AGENT_INSTALLERS = {
  "claude": ClaudeInstaller(...),
  "codex": CodexInstaller(...),
  "goose": GooseInstaller(...),
  "kimi": KimiInstaller(...),
}
```

3. CLI allowlist: `install-proxy` / `uninstall-proxy` accept `goose|kimi`
4. `service stop` warning text lists all proxy agents

---

## 9. Hooks vs proxy inject

### 9.1 Policy (updated from dual-path spec)

When `is_proxy_agent(agent)` is **true** for the detected hook agent:

- **Skip** hook-side memory inject (return empty / status-only response)
- Proxy performs **one** inject via `inject_memory()` before forward

### 9.2 Project scoping fix (required for skip)

Proxy inject currently weak without `cwd`. Mitigations for 0.11:

| Agent | Project hint source |
|-------|---------------------|
| Goose | Hook already uses `Path.cwd()` when `working_dir` missing; proxy should read `x-memor-project` if set, else same cwd heuristic via optional header `x-memor-cwd` stamped at install in custom headers **or** infer from Goose session DB on daemon path only (not on hot path) |
| Kimi | Kimi hook payloads include workspace context when available; stamp `x-memor-project` from install if Kimi supports custom headers on provider requests |
| Claude | Existing `x-memor-project` / env patterns |

**Minimum bar for 0.11:** stamp `x-agent` + document that users should run Goose/Kimi from project root; accept `unknown` project fallback same as today’s proxy behavior. Re-enable skip only when `x-agent` stamping verified in tests.

### 9.3 Code change

In `memor/hook_server.py`, replace the “deliberately still recalls” block with:

```python
from memor.config import is_proxy_agent
if is_proxy_agent(agent):
    return format_hook_response(agent, "")  # or status-only line
```

---

## 10. Runtime fail-open shim

### 10.1 Behavior

On each request, after agent/upstream resolution:

1. Try `run_pipeline()` + `inject_memory()`
2. On **any** pipeline/inject exception OR global `compressor_ready=False`:
   - Log warning
   - Forward **original** request body (pre-pipeline) to captured upstream
   - Record ledger row with `passthrough=1`, `tokens_before == tokens_after`
   - Set health `mode=passthrough` until next successful compress

Per-request pipeline errors already passthrough in spirit; shim generalizes to **process-level** degrade (embedder missing, repeated failures).

### 10.2 Implementation

**In-process on `:8421`** (no second port):

- `create_proxy_app()` holds a module-level `CompressorState`
- Same FastAPI app serves both modes
- Streaming: shim must still stream SSE unchanged

### 10.3 Not in scope

- Separate watchdog process rewriting configs mid-session
- Automatic revert to direct upstream URLs in agent config files (that stays config failover)

---

## 11. Dashboard — per-agent proxy savings

### 11.1 API

New endpoint: `GET /api/proxy-savings-by-agent?days=30`

```json
{
  "agents": [
    {
      "agent": "goose",
      "tokens_before": 120000,
      "tokens_after": 98000,
      "pct_saved": 18.3,
      "requests": 42,
      "passthrough_requests": 2
    }
  ]
}
```

SQL: `GROUP BY agent` on `proxy_savings` where `passthrough=0` for savings calc; report passthrough count separately.

### 11.2 UI

Extend **System Status** metric grid or **Agent Breakdown** section:

- Per proxied agent: `% saved`, before→after tokens, request count
- Clear label: **Proxy savings** (distinct from hook recall metrics in existing Agent Breakdown)

Show aggregate Token Savings hero unchanged (all agents).

Show proxy `mode` from extended `/health` or `/api/proxy-status`.

---

## 12. Testing

| Area | Tests |
|------|-------|
| Goose install | Custom JSON rewrite, OpenAI env rewrite, backup roundtrip, missing provider fail |
| Kimi install | TOML base_url rewrite, custom_headers merge, anthropic vs openai type |
| Upstream routing | Same port, different `x-agent` → different upstream URLs (mock httpx) |
| Shim | Pipeline raises → forward original body, `passthrough=1`, health mode |
| Failover | `failover_proxy_agents` restores goose+kimi; flags cleared |
| Hooks | Proxied agent skips inject; non-proxied still injects |
| Dashboard | `/api/proxy-savings-by-agent` aggregation |
| Ledger | `agent=goose|kimi` when header set |

---

## 13. Release (0.11.0)

- Bump version in `pyproject.toml` → `0.11.0`
- CHANGELOG: Goose/Kimi proxy, per-agent savings, runtime shim, lifecycle (#42)
- README + CLI help: `install-proxy --agent goose|kimi`
- Do not market compression percentages until user has trusted ledger data (same honesty bar as dual-path)

---

## 14. Open questions (resolved in this spec)

| Question | Resolution |
|----------|------------|
| Upstream model | **C** — capture at install |
| Goose identity | `x-agent: goose` at install (not inferred from upstream) |
| Hook skip | Yes, with project scoping mitigations §9 |
| Shim vs config failover | Both; different triggers §3 |
| 0.11 vs 0.12 | **B** — Cursor + crushers in 0.12 |

---

## 15. Self-review checklist

- [x] Supersedes lifecycle D2/D3 explicitly
- [x] Multi-agent upstream map on one port
- [x] Goose URL shape matrix (custom vs built-in)
- [x] Kimi protocol detection
- [x] Missing Goose provider file failure mode
- [x] Hook skip contradicts current code — policy updated + scoping noted
- [x] Shim architecture: in-process, same port, health mode
- [x] Dashboard uses `proxy_savings`, not `recall_log`
- [x] Failover registry extension for goose/kimi
- [x] Passthrough ledger semantics for honest savings
- [x] 0.12 parking lot explicit
- [x] MCP registration called out (best-effort)

---

**Next step after approval:** implementation plan via `writing-plans` → branch `feat/goose-kimi-proxy-0.11`.
