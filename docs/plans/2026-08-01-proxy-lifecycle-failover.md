# Proxy Lifecycle + Config Failover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the Memor proxy running across `service restart`/`install` when opted in, verify health after `install-proxy`, and config-failover agents off a dead `:8421` when unhealthy or on `service uninstall`.

**Architecture:** Infer `with_proxy` from `proxy_agents` or existing proxy unit file inside `service.install()`. Add `wait_for_proxy_health` (Memor JSON `/health`, 45s). Add uninstall-shaped `failover_proxy_agents()` used by failed `install-proxy` and `service.uninstall`. `stop()` warns only.

**Tech Stack:** Python 3.11+, existing `memor.service` / `memor.proxy.install` / `httpx` or stdlib `urllib`, pytest.

**Spec:** `docs/plans/2026-08-01-proxy-lifecycle-failover-design.md` (tracked copy of approved design).

## Global Constraints

- No Goose/Kimi/Cursor proxy install in this PR
- No always-on passthrough shim; no runtime watchdog
- Health success = HTTP 200 + JSON `ok is True`
- Failover = restore backup + unlink backup + clear `proxy_agents` flag
- `stop` warns; does not failover
- Keep existing tests green; extend `tests/test_service_proxy_unit.py` and add focused new tests

## File map

| File | Role |
|------|------|
| `memor/service.py` | `_should_run_proxy`, install inference, stop warning, uninstall failover |
| `memor/proxy/health.py` | `wait_for_proxy_health(port, timeout=45.0) -> tuple[bool, str]` |
| `memor/proxy/install.py` | `failover_proxy_agents(reason: str) -> list[str]` |
| `memor/cli.py` | `install_proxy` health gate + failover + exit 1 |
| `tests/test_proxy_health.py` | health probe unit tests |
| `tests/test_proxy_failover.py` | failover restore/strip tests |
| `tests/test_service_proxy_unit.py` | restart/inference/stop/uninstall |
| `README.md` | short reliability blurb |

---

### Task 1: `_should_run_proxy` + install inference

**Files:**
- Modify: `memor/service.py`
- Test: `tests/test_service_proxy_unit.py`

**Produces:** `_should_run_proxy() -> bool`; `install()` ORs in inferred proxy

- [ ] Write failing tests: default `install` with `proxy_agents.claude=True` bootstraps proxy; with proxy plist present and flags empty bootstraps proxy; with neither does not; `restart` after proxied install re-bootstraps proxy
- [ ] Implement `_should_run_proxy` + wire into `install`
- [ ] Run `pytest tests/test_service_proxy_unit.py -q`
- [ ] Commit

### Task 2: Health probe

**Files:**
- Create: `memor/proxy/health.py`
- Test: `tests/test_proxy_health.py`

**Produces:** `wait_for_proxy_health(port: int, timeout: float = 45.0, interval: float = 0.5) -> tuple[bool, str]`

- [ ] Failing tests: Memor JSON ok → True; other 200 → False; connection fail until timeout → False (use short timeout in test)
- [ ] Implement with urllib or httpx
- [ ] `pytest tests/test_proxy_health.py -q`
- [ ] Commit

### Task 3: `failover_proxy_agents`

**Files:**
- Modify: `memor/proxy/install.py`
- Test: `tests/test_proxy_failover.py`

**Produces:** `failover_proxy_agents(reason: str = "") -> list[str]`

- [ ] Failing tests: with backup restores file + clears flag + deletes backup; without backup strips memor localhost base URL keys + clears flag
- [ ] Implement; reuse `_agent_paths` / uninstall logic
- [ ] `pytest tests/test_proxy_failover.py -q`
- [ ] Commit

### Task 4: Wire CLI + stop + uninstall

**Files:**
- Modify: `memor/cli.py` (`install_proxy`)
- Modify: `memor/service.py` (`stop`, `uninstall`)
- Test: extend unit tests / small CLI test if pattern exists

- [ ] `install_proxy`: after `service.install`, health check; on fail failover + exit 1
- [ ] `stop`: append warning when any `proxy_agents`
- [ ] `uninstall`: call `failover_proxy_agents` when any `proxy_agents` before/after removing units
- [ ] Tests for warning + uninstall failover mock
- [ ] Commit

### Task 5: README + design copy

**Files:**
- Modify: `README.md` (service restart / proxy reliability)
- Create: `docs/plans/2026-08-01-proxy-lifecycle-failover-design.md` (tracked spec copy)

- [ ] Docs blurb
- [ ] Full pytest subset for proxy/service
- [ ] Commit

---

**Done when:** success criteria in the design spec §7 hold; ready for PR (no version bump unless asked).
