# Proxy Lifecycle + Config Failover — Design Spec

**Date:** 2026-08-01  
**Status:** Draft for user review  
**Product:** memor-cli / Memor  
**Goal:** Proxy savings stay available after install/upgrade/restart, and agents never stay pointed at a dead `:8421` when Memor cannot serve the proxy.

---

## 1. Problem

 Dual-path v1 left a reliability hole:

1. **`memor service restart`** (the documented post-`pipx upgrade` path) calls `stop()` then `install(with_proxy=False)`. The proxy plist/unit file remains, but launchd/systemd no longer runs the process. Status shows `proxy: installed, not running`.
2. Agents still have `ANTHROPIC_BASE_URL` / `openai_base_url` aimed at `http://127.0.0.1:8421` → **no savings** and risk of hard API failures.
3. **`install-proxy`** rewrites agent config before verifying the proxy is healthy; there is no `/health` gate.
4. **`service uninstall`** can remove the proxy unit while leaving agent base URLs pointed at localhost.

We choose **fail-closed on the wire** (no always-on passthrough shim) and **config failover** when the proxy cannot be made healthy: restore pre-proxy agent config so coding continues against Anthropic/OpenAI directly.

---

## 2. Decisions (locked)

| # | Decision |
|---|----------|
| D1 | Lifecycle: auto-`with_proxy=True` when any `proxy_agents` is true **or** the proxy unit file already exists |
| D2 | Fail-open = **config failover** (restore backups), not an always-on shim |
| D3 | Goose / Kimi `install-proxy` = **out of scope** (follow-up) |
| D4 | No mid-session background watchdog in this PR |
| D5 | Health must validate Memor’s JSON body; long poll to avoid false failover on cold start |

---

## 3. Behavior

### 3.1 Infer `with_proxy` (`memor/service.py`)

Add `def _should_run_proxy() -> bool`:

- `True` if any value in `load_config()["proxy_agents"]` is truthy, **or**
- `True` if the proxy plist (macOS) / systemd unit file (Linux) already exists.

`install(with_dashboard=..., with_proxy=...)`:

- If caller passes `with_proxy=True`, keep True.
- If caller passes `with_proxy=False` (default), **override to True** when `_should_run_proxy()`.

`restart()` remains `stop()` + `install()` with defaults — inference pulls proxy back when opted in.

CLI `memor service install` uses the same `install()` path (no extra flag required).

### 3.2 Health probe

New helper (e.g. `memor/proxy/health.py` or `service.wait_for_proxy_health`):

- `GET http://127.0.0.1:{proxy_port}/health`
- Success only if HTTP 200 **and** JSON parses with `ok is True` (Memor shape). Bare TCP connect is insufficient.
- Poll every ~0.5s for up to **45 seconds** (cold start / model fetch).
- Return `{ok: bool, detail: str}`.

### 3.3 Config failover (uninstall-shaped)

New `failover_proxy_agents(reason: str) -> list[str]` in `memor/proxy/install.py` (or shared helper):

For each agent with `proxy_agents[agent] is True` (claude, codex today):

1. Restore config from `~/.memor/proxy-backup-<agent>.*` if present (same as `uninstall_agent_proxy`).
2. Unlink the backup (consume it) so the next `install-proxy` snapshots a fresh pre-proxy config.
3. `set_proxy_agent(agent, False)`.
4. Collect human-readable lines for the CLI.

If backup missing: strip known proxy keys if safe (`ANTHROPIC_BASE_URL` pointing at memor port / `openai_base_url` pointing at memor port); still clear the flag; warn that manual check may be needed.

### 3.4 `install-proxy` flow

1. Install agent config + set `proxy_agents` (existing).
2. `service.install(with_dashboard=True, with_proxy=True)`.
3. Health probe (3.2).
4. **If healthy:** print ready message (exit 0).
5. **If unhealthy:** run failover (3.3); print clear error that direct API was restored; **exit non-zero**.

### 3.5 `service stop`

Do **not** auto-failover (stopping for maintenance should not silently uninstall proxy intent).

Do print a loud warning when `_should_run_proxy()` / any `proxy_agents`:

```
warning: proxy-enabled agents still point at http://127.0.0.1:{port}.
  Start again: memor service restart
  Or restore direct API: memor uninstall-proxy --agent <agent>
```

### 3.6 `service uninstall`

If any `proxy_agents` is true **or** proxy unit existed with agents still flagged:

- Run config failover (3.3) before/after removing units so agents are not stranded.
- Message: proxy configs restored to direct API.

### 3.7 Out of scope

- Always-on passthrough shim on `:8421`
- Runtime watchdog that failovers hours after a crash
- Goose / Kimi / Cursor proxy install
- Changing compress pipeline or upstream fail-open inside a live proxy
- Auto re-enable after failover without user `install-proxy`

---

## 4. Dashboard / status (minimal)

No new UI required. Existing `/api/proxy-status` already reflects TCP/health + `proxy_agents`. After failover, `proxy_agents` empty/false and proxy may be not installed — consistent.

Optional one-line in `memor service status` when plist exists but not running and any agent still flagged (should be rare after this PR): suggest `memor service restart` or `uninstall-proxy`. Nice-to-have; not blocking.

---

## 5. Tests

| Case | Expect |
|------|--------|
| `restart` after `install(with_proxy=True)` | proxy unit bootstrapped again (`with_proxy` inferred) |
| `install()` default when `proxy_agents.claude=True` | `_units` includes proxy |
| `install()` default when only proxy plist exists, flags false | still includes proxy (file exists) — **or** document that flags win; prefer: file exists ⇒ with_proxy so upgrade path works even if config was hand-edited |
| Health helper accepts Memor JSON; rejects other 200 bodies | |
| `install-proxy` unhealthy → failover restores backup, flag false, exit 1 | mock health fail |
| `uninstall` with proxy_agents set → failover called | |
| `stop` with proxy_agents set → warning string present; flags unchanged | |

---

## 6. Docs

README / help:

- After `pipx upgrade`, `memor service restart` keeps the proxy when it was opted in.
- If the proxy cannot become healthy, Memor restores agent API URLs so calls do not hang on localhost.
- `memor service stop` warns that agents may still point at the proxy port.

---

## 7. Success criteria

1. Reproduce old bug (install-proxy → service restart → proxy not running) → **fixed**.
2. Forced unhealthy proxy after install-proxy → agent config restored, exit ≠ 0, Claude not left on `:8421`.
3. `service uninstall` with Claude proxied → Claude base URL restored.
4. Existing proxy unit tests still pass; new cases above green.

---

## 8. Spec self-review

| Check | Result |
|-------|--------|
| Placeholder / TBD | None material |
| Contradictions | stop warns (no failover) vs uninstall/install-proxy failover — intentional |
| Ambiguity | Failover = uninstall-shaped restore + consume backup — specified |
| Scope | Goose/shim/watchdog explicitly out |
| False failover | 45s poll + JSON `ok` — specified |
| Port impostor | JSON shape required — specified |

**Residual risk:** Proxy crash mid-session still breaks until user restarts or uninstalls-proxy (D4). Accept for this PR.

---

## 9. Implementation sketch (non-binding)

1. `_should_run_proxy()` + wire into `install()` / tests  
2. `wait_for_proxy_health(port, timeout=45)`  
3. `failover_proxy_agents()` shared by CLI install-proxy failure path + `service.uninstall`  
4. Warning branch in `stop()`  
5. README blurb  
6. Patch release after merge (e.g. 0.10.2)
