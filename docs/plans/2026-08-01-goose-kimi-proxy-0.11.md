# Goose + Kimi Proxy 0.11 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship 0.11.0 with Goose/Kimi proxy install, per-agent upstream routing, runtime fail-open shim, hook skip when proxied, and per-agent proxy savings on the dashboard.

**Architecture:** Extend `~/.memor/config.json` with `proxy_upstreams` per agent. Proxy resolves `x-agent` header → upstream URL. Install helpers capture pre-proxy URLs for Goose (`custom_providers` JSON / env) and Kimi (`config.toml`). Compressor failures forward original body (shim). Dashboard aggregates `proxy_savings` by agent.

**Tech Stack:** Python 3.11+, FastAPI, httpx, Typer, pytest, FastAPI TestClient. Existing `memor/proxy/*`, `memor/config.py`, `memor/hook_server.py`, dashboard static HTML.

**Spec:** `docs/plans/2026-08-01-goose-kimi-proxy-0.11-design.md`

## Global Constraints

- Proxy binds `127.0.0.1` only; refuse `0.0.0.0`.
- Default proxy port: `8421`.
- Never persist API keys; forward `Authorization` unchanged.
- `proxy_upstreams` written at install from captured agent config; never guessed.
- Ledger passthrough rows (`passthrough=1`) excluded from `% saved` calculations.
- Health returns `ok: true` in both `compress` and `passthrough` modes.
- Hook skip when `is_proxy_agent(agent)` is true.
- Config failover (#42) unchanged; shim is runtime-only.
- Out of scope: Cursor compression, new crushers, multimodal (0.12).

---

## File structure

| Path | Change |
|------|--------|
| `memor/config.py` | Add `proxy_upstreams` get/set helpers |
| `memor/proxy/upstream.py` | **New:** `resolve_agent()`, `resolve_upstream_url()` |
| `memor/proxy/shim.py` | **New:** `CompressorState`, shim forward helper |
| `memor/proxy/server.py` | Per-agent upstream, shim, extended `/health` |
| `memor/proxy/install.py` | Registry + Goose/Kimi installers; upstream capture for Claude/Codex |
| `memor/proxy/goose_install.py` | **New:** Goose discovery/rewrite |
| `memor/proxy/kimi_install.py` | **New:** Kimi discovery/rewrite |
| `memor/hook_server.py` | Skip inject when proxied |
| `memor/cli.py` | Allow `goose|kimi` for install/uninstall-proxy |
| `memor/store/sqlite_store.py` | `get_proxy_savings_by_agent()` |
| `memor/dashboard/server.py` | `/api/proxy-savings-by-agent`, extend proxy-status |
| `memor/dashboard/static/index.html` | Per-agent proxy savings UI |
| `tests/test_proxy_upstream.py` | **New** |
| `tests/test_proxy_shim.py` | **New** |
| `tests/test_goose_proxy_install.py` | **New** |
| `tests/test_kimi_proxy_install.py` | **New** |
| `tests/test_hook_proxy_skip.py` | **New** |
| `tests/test_dashboard_proxy.py` | Extend for by-agent endpoint |

---

### Task 1: `proxy_upstreams` config helpers

**Files:**
- Modify: `memor/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `get_proxy_upstream(agent: str) -> dict | None`
  - `set_proxy_upstream(agent: str, *, protocol: str, base_url: str, provider_name: str = "") -> None`
  - `clear_proxy_upstream(agent: str) -> None`
  - `load_config()` merges `proxy_upstreams: {}` default

- [ ] **Step 1: Write failing tests**

```python
def test_proxy_upstream_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
    assert cfg.get_proxy_upstream("goose") is None
    cfg.set_proxy_upstream("goose", protocol="openai",
        base_url="https://api.deepseek.com/v1/chat/completions",
        provider_name="custom_deepseek")
    u = cfg.get_proxy_upstream("goose")
    assert u["protocol"] == "openai"
    assert u["base_url"].endswith("/chat/completions")
    cfg.clear_proxy_upstream("goose")
    assert cfg.get_proxy_upstream("goose") is None
```

- [ ] **Step 2: Run** `pytest tests/test_config.py -v` — expect FAIL

- [ ] **Step 3: Implement** helpers in `memor/config.py`

- [ ] **Step 4: Run** `pytest tests/test_config.py -v` — expect PASS

- [ ] **Step 5: Commit** `feat(config): add proxy_upstreams helpers`

---

### Task 2: Upstream routing module

**Files:**
- Create: `memor/proxy/upstream.py`
- Modify: `memor/proxy/server.py` (wire Anthropic + OpenAI routes)
- Test: `tests/test_proxy_upstream.py`

**Interfaces:**
- Consumes: `get_proxy_upstream`, `load_config`, `is_proxy_agent` from `memor.config`
- Produces:
  - `resolve_agent(headers: Mapping[str, str]) -> str`
  - `resolve_upstream_url(agent: str, protocol: str) -> str | None`
  - Legacy fallback: if agent unknown and only one upstream for protocol, use it; else if agent is `claude`/`codex` and no entry, use hardcoded defaults for backward compat

- [ ] **Step 1: Write failing tests** — mock config with two agents, assert different URLs per `x-agent`

- [ ] **Step 2: Run** `pytest tests/test_proxy_upstream.py -v` — FAIL

- [ ] **Step 3: Implement** `upstream.py` + replace hardcoded URLs in `server.py`

- [ ] **Step 4: Run** `pytest tests/test_proxy_upstream.py tests/test_proxy_server.py -v` — PASS

- [ ] **Step 5: Commit** `feat(proxy): per-agent upstream routing`

---

### Task 3: Runtime fail-open shim + extended health

**Files:**
- Create: `memor/proxy/shim.py`
- Modify: `memor/proxy/server.py`, `memor/proxy/health.py` (optional: export mode check)
- Test: `tests/test_proxy_shim.py`

**Interfaces:**
- Produces:
  - `class CompressorState` with `mode: Literal["compress","passthrough"]`, `compressor_ready: bool`
  - `async def forward_with_shim(...)` — try pipeline; on exception use original body, `passthrough=1`

- [ ] **Step 1: Write failing test** — patch `run_pipeline` to raise; assert forward uses original body and ledger `passthrough=1`

- [ ] **Step 2: Run** `pytest tests/test_proxy_shim.py -v` — FAIL

- [ ] **Step 3: Implement** shim in server endpoints; extend `/health` with `mode` and `compressor_ready`

- [ ] **Step 4: Run** `pytest tests/test_proxy_shim.py tests/test_proxy_health.py -v` — PASS

- [ ] **Step 5: Commit** `feat(proxy): runtime fail-open shim`

---

### Task 4: Install registry + Claude/Codex upstream capture

**Files:**
- Modify: `memor/proxy/install.py`
- Modify: `tests/test_proxy_install.py`

**Interfaces:**
- Produces:
  - `AGENT_PROXY_HANDLERS: dict[str, AgentProxyHandler]` with keys `claude`, `codex`, `goose`, `kimi`
  - `install_agent_proxy(agent: str, port: int) -> None`
  - `uninstall_agent_proxy(agent: str) -> None` (existing, extended)
  - Claude install captures `https://api.anthropic.com/v1/messages` before rewrite (or reads from backup/env if present)
  - Codex install captures pre-proxy openai base URL

- [ ] **Step 1: Write failing tests** — after `install_claude_proxy`, assert `get_proxy_upstream("claude")` populated

- [ ] **Step 2: Run** `pytest tests/test_proxy_install.py -v -k upstream` — FAIL

- [ ] **Step 3: Refactor** install.py to registry; populate upstream on claude/codex

- [ ] **Step 4: Run** `pytest tests/test_proxy_install.py tests/test_proxy_failover.py -v` — PASS

- [ ] **Step 5: Commit** `feat(proxy): install registry and upstream capture`

---

### Task 5: Goose proxy install/uninstall/failover

**Files:**
- Create: `memor/proxy/goose_install.py`
- Modify: `memor/proxy/install.py` (register goose handler)
- Modify: `memor/cli.py` (allow goose)
- Test: `tests/test_goose_proxy_install.py`

**Interfaces:**
- Produces:
  - `discover_goose_upstream() -> tuple[protocol, base_url, provider_name, rewrite_kind]`
  - `install_goose_proxy(port: int) -> None`
  - `uninstall_goose_proxy() -> None`
  - `strip_goose_proxy_urls(port: int) -> str`
  - Custom JSON: rewrite `base_url`, merge `headers.x-agent=goose`
  - Missing provider file: raise `GooseProviderNotFoundError` with actionable message

- [ ] **Step 1: Write failing tests** with tmp HOME + fake `custom_providers/custom_foo.json`

- [ ] **Step 2: Run** `pytest tests/test_goose_proxy_install.py -v` — FAIL

- [ ] **Step 3: Implement** goose_install.py + wire registry + CLI

- [ ] **Step 4: Run** `pytest tests/test_goose_proxy_install.py tests/test_proxy_failover.py -v` — PASS

- [ ] **Step 5: Commit** `feat(proxy): Goose install-proxy support`

---

### Task 6: Kimi proxy install/uninstall/failover

**Files:**
- Create: `memor/proxy/kimi_install.py`
- Modify: `memor/proxy/install.py`, `memor/cli.py`
- Test: `tests/test_kimi_proxy_install.py`

**Interfaces:**
- Reuse `kimi_config_path()` from `memor/cli.py` or duplicate minimal path helper in kimi_install
- Produces:
  - `discover_kimi_upstream(config_path: Path) -> ...`
  - `install_kimi_proxy(port: int) -> None`
  - TOML rewrite: provider `base_url`, merge `custom_headers.x-agent=kimi`
  - Missing `base_url`: raise clear error

- [ ] **Step 1: Write failing tests** with sample `config.toml`

- [ ] **Step 2: Run** `pytest tests/test_kimi_proxy_install.py -v` — FAIL

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run** `pytest tests/test_kimi_proxy_install.py tests/test_proxy_failover.py -v` — PASS

- [ ] **Step 5: Commit** `feat(proxy): Kimi install-proxy support`

---

### Task 7: Hook skip when proxied

**Files:**
- Modify: `memor/hook_server.py`
- Test: `tests/test_hook_proxy_skip.py`

- [ ] **Step 1: Write failing test** — `set_proxy_agent("goose", True)`, goose UserPromptSubmit → empty additionalContext

- [ ] **Step 2: Run** `pytest tests/test_hook_proxy_skip.py -v` — FAIL

- [ ] **Step 3: Add** `is_proxy_agent` check before recall; return empty/status-only response

- [ ] **Step 4: Run** `pytest tests/test_hook_proxy_skip.py tests/test_multi_agent_hook.py -v` — PASS

- [ ] **Step 5: Commit** `feat(hooks): skip inject for proxied agents`

---

### Task 8: Dashboard per-agent proxy savings

**Files:**
- Modify: `memor/store/sqlite_store.py`
- Modify: `memor/dashboard/server.py`
- Modify: `memor/dashboard/static/index.html`
- Modify: `tests/test_dashboard_proxy.py`

**Interfaces:**
- Produces: `get_proxy_savings_by_agent(days: int) -> list[dict]`
- API: `GET /api/proxy-savings-by-agent?days=30`
- UI: show per-agent rows under System Status or Agent Breakdown with label "Proxy savings"

- [ ] **Step 1: Write failing test** for store + API

- [ ] **Step 2: Run** `pytest tests/test_dashboard_proxy.py -v -k by_agent` — FAIL

- [ ] **Step 3: Implement** store query, endpoint, minimal UI rows

- [ ] **Step 4: Run** `pytest tests/test_dashboard_proxy.py -v` — PASS

- [ ] **Step 5: Commit** `feat(dashboard): per-agent proxy savings`

---

### Task 9: Release 0.11.0 docs + version bump

**Files:**
- Modify: `pyproject.toml` (version `0.11.0`)
- Modify: `README.md`, `memor/cli.py` help strings
- Modify: `CHANGELOG.md` or create entry if missing

- [ ] **Step 1: Bump** version to `0.11.0`

- [ ] **Step 2: Update** README install-proxy agents list: `claude, codex, goose, kimi`

- [ ] **Step 3: Add** CHANGELOG section for 0.11.0

- [ ] **Step 4: Run** full test suite `pytest -q`

- [ ] **Step 5: Commit** `chore: release 0.11.0`

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| proxy_upstreams schema | 1 |
| x-agent routing | 2 |
| Shim + health mode | 3 |
| Goose install | 5 |
| Kimi install | 6 |
| Claude/Codex upstream capture | 4 |
| Failover registry | 4, 5, 6 |
| Hook skip | 7 |
| Dashboard by-agent | 8 |
| Release 0.11.0 | 9 |
| MCP best-effort | Deferred minor — document in CHANGELOG if not done |
| upstream_label column | Deferred — use existing ledger fields for v1 |
