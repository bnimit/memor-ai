# Cursor Full-Install Automation (Phase 3)

**Date:** 2026-08-02  
**Status:** Implemented (2026-08-02) — see `memor/cursor_wire/install.py`, `memor/service.py`  
**Depends on:** Phase 2 wire compress (`memor/cursor_wire/`, dashboard Cursor Wire)
**Self-reviewed:** 2026-08-02

## Goal

One command turns on **all Cursor-related Memor features** with minimal intervention:

```bash
memor install-proxy --agent cursor
```

User sees a clear explanation, confirms once, may enter Touch ID / password **once** for CA trust, then restarts Cursor. No manual mitmweb, no hand-editing settings, no orphaned mitmdump after upgrades.

## Product decision

| Decision | Choice |
|---|---|
| Entry point | Extend `memor install-proxy --agent cursor` (full Cursor stack) |
| Wire binary | Headless **`mitmdump`** only (not mitmweb) |
| CA trust | Informed consent + one `sudo` / Touch ID |
| Port 8080 busy | **Auto-pick** next free port; sync into Cursor settings + config |
| Service lifecycle | Wire unit follows `memor service` install/restart/stop/uninstall |
| Uninstall | Restore Cursor settings; stop wire; offer CA removal note |

## What “full Cursor stack” installs

1. **Memory hooks** — ensure Claude/Cursor hook path is installed if missing  
2. **Shell compress hooks** — `install-cursor-compress-hooks` (idempotent)  
3. **BYOK proxy** — existing `:8421` path + base URL keys (unchanged semantics)  
4. **Subscription wire** — `mitmdump` + Memor addon on wire port  
5. **Cursor `settings.json`** — proxy + BYOK keys (backup first)  
6. **CA trust** — generate mitm CA if needed; add to system trust with consent  
7. **launchd/systemd unit** — `ai.memor.cursor-wire` so upgrades/restarts recycle mitmdump  

## User-facing install flow

```text
memor install-proxy --agent cursor

→ Explain:
    Enables full Cursor support:
    • Memory recall (hooks)
    • Shell output compression
    • BYOK proxy on 127.0.0.1:8421
    • Subscription wire compression via local MITM
      (decrypts Cursor → *.cursor.sh on this machine only)

→ Prompt: Continue? [Y/n]

→ Ensure memor-cli[cursor-wire] / mitmproxy available
   (pip/pipx hint if mitmdump missing; do not bundle full mitmweb UI)

→ Ensure ~/.mitmproxy/mitmproxy-ca-cert.pem exists
   (run mitmdump briefly or certutil equivalent if missing)

→ Explain CA step:
    "About to add the Memor Cursor wire CA to your system trust store
     so HTTPS interception works. macOS will ask for Touch ID / password once."

→ Prompt: Trust CA now? [Y/n]
   Y → sudo security add-trusted-cert … (macOS)
       Linux: document trust steps / best-effort update-ca-certificates
   n → abort wire portion; still offer BYOK + Shell hooks, warn Composer wire off

→ Resolve wire listen port (see Port selection)
→ Backup Cursor settings.json → ~/.memor/proxy-backup-cursor.json (before any writes)
→ If existing non-Memor http.proxy → warn + confirm replace (G5)

→ Write BYOK base URL keys for :8421 (existing)
→ Config: proxy_agents.cursor = true; cursor_wire_port = <port>

→ Start cursor-wire unit (mitmdump via absolute/sys.executable path — G4)
→ Health probe wire port (G2 / G15); on failure → skip wire keys, warn, BYOK+hooks only

→ On healthy wire, write Cursor wire settings:
    http.proxy = http://127.0.0.1:<wire_port>
    http.proxySupport = override
    http.proxyStrictSSL = false
    cursor.general.disableHttp2 = true
    http.noProxy / proxyBypassList = 127.0.0.1,localhost,::1  (G1)
→ cursor_wire = true

→ install-cursor-compress-hooks; ensure memory hooks if missing (G14)
→ Print: Restart Cursor · dashboard → Cursor / Cursor Wire
```

## Port selection (locked)

Default candidate: **8080**.

1. If `MEMOR_CURSOR_WIRE_PORT` set → use it (fail if busy).  
2. Else if `config.cursor_wire_port` already set and free → reuse (stable across restarts).  
3. Else scan `8080–8090` for first free `127.0.0.1` bind (skip nothing unless we add mitmweb later).  
4. Persist chosen port to `cursor_wire_port` and write matching `http.proxy` into Cursor.  
5. Install output must state when falling back:  
   `Port 8080 in use; using 8082. Cursor settings updated.`  
6. If no port in range is free → **fail wire install** with clear error; do not point Cursor at a dead port. Optionally continue with BYOK + Shell hooks only and `cursor_wire = false`.

Same port must be used by: Cursor settings, launchd args, and `memor cursor-wire-mitm --dump --port …`.

## Service lifecycle (locked)

Label: `ai.memor.cursor-wire`  
Command: `memor cursor-wire-mitm --dump --port <cursor_wire_port>`  
Log: `~/.memor/cursor-wire.log`

| Command | Behavior |
|---|---|
| `memor install-proxy --agent cursor` | Sets `cursor_wire=true`, installs unit, starts mitmdump |
| `memor service install` / `restart` | Includes wire unit when `_should_run_cursor_wire()` |
| `memor service stop` | Stops wire with daemon/dashboard/proxy |
| `memor service uninstall` | Unloads wire plist/unit |
| `memor uninstall-proxy --agent cursor` | Stops wire, clears flags, restores Cursor settings |
| `pipx upgrade` + `memor service restart` | New code + recycled mitmdump — no manual mitm |

`_should_run_cursor_wire()` mirrors proxy: true when `config.cursor_wire` is set (survives upgrades).

## Cursor config updates (locked)

**Wire keys** (always when wire enabled):

```json
{
  "http.proxy": "http://127.0.0.1:<wire_port>",
  "http.proxySupport": "override",
  "http.proxyStrictSSL": false,
  "cursor.general.disableHttp2": true
}
```

**BYOK keys** (existing): OpenAI/Anthropic base URL → Memor `:8421` Cursor-prefixed paths.

**Backup / restore:** Never leave Cursor pointed at localhost after uninstall. Restore backup or strip Memor-owned keys only.

**Post-install:** User must restart Cursor once (document clearly; cannot automate IDE reload reliably).

## CA trust (locked)

- Cert path: `~/.mitmproxy/mitmproxy-ca-cert.pem`  
- Never silent: always explain + confirm before trusting  
- macOS: `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain <pem>`  
- Touch ID / password once is acceptable  
- Uninstall: stop intercept; print how to remove CA from Keychain (optional automated removal with second confirm)

## Dependency

- Optional extra: `memor-cli[cursor-wire]` → `mitmproxy`  
- Runtime uses **`mitmdump` only** (thin headless service)  
- Do not require mitmweb for production path  
- If mitmdump missing at install: clear install instructions (`pipx inject memor-cli mitmproxy` or `pip install 'memor-cli[cursor-wire]'`)

## Uninstall flow

```bash
memor uninstall-proxy --agent cursor
```

1. Stop/unload `ai.memor.cursor-wire`  
2. Restore Cursor settings backup / strip Memor keys (wire + BYOK)  
3. `cursor_wire = false`, clear `proxy_agents.cursor`  
4. Note on CA removal  
5. Shell compress hooks: leave installed unless user runs `uninstall-cursor-compress-hooks` (or offer combined flag later)

## Dashboard / ledger

No change to attribution model: wire savings → `agent=cursor-wire`, Cursor desk rolls them up. After install, dashboard should show activity once Composer traffic flows.

### Cursor wire health on the UI (locked)

Surface mitmdump alongside existing System Status chips (Proxy / Hook / Daemon):

| UI | Behavior |
|---|---|
| Status chip **Cursor Wire** | On / Off / Degraded — from `/api/proxy-status` (or dedicated field) |
| Overview | Show chip when `cursor_wire` opted in **or** wire unit/port responds; hide when never installed |
| Cursor agent desk | Small health line: `Wire mitmdump :{port} · healthy` / `down — Composer may hang; run memor service restart` |

**API:** Extend `/api/proxy-status` with e.g.:

```json
{
  "cursor_wire": {
    "enabled": true,
    "running": true,
    "port": 8080,
    "healthy": true,
    "detail": "tcp ok"
  }
}
```

**Health probe (same helper as install/restart):** TCP connect to `127.0.0.1:{cursor_wire_port}` (optional short CONNECT). Not the BYOK `:8421/health` JSON. Dashboard polls with the normal 30s refresh.

**Degraded:** `enabled && !healthy` → warn color on chip + copy pointing at `memor service restart` / `failover` if settings still point at a dead port.

## Tests

- Port picker: busy 8080 → selects next free; config + settings URL match  
- `_should_run_cursor_wire` gates unit inclusion  
- Settings write/restore round-trip for wire keys  
- Install dry-run / unit tests without real sudo (mock CA step)  
- Service restart keeps wire when flag set  
- `/api/proxy-status` includes `cursor_wire` health; dashboard HTML shows Cursor Wire chip when enabled  

## Non-goals

- Auto-restart Cursor IDE  
- Silent CA install without prompt  
- Bundling mitmweb UI into default UX  
- Reimplementing TLS MITM without mitmproxy  

## Success criteria

1. Fresh machine: one `memor install-proxy --agent cursor` + CA Touch ID + Cursor restart → Composer wire compression works  
2. `memor service restart` after upgrade recycles mitmdump without manual steps  
3. Occupied 8080 does not break install; Cursor and service agree on port  
4. Uninstall restores Cursor network settings  

## Implementation order

1. Config helpers (`cursor_wire`, `cursor_wire_port`) + port picker  
2. Settings write/restore for wire keys  
3. CA ensure + trust helpers (macOS first)  
4. launchd/systemd unit + `_should_run_cursor_wire` in `service.py`  
5. Wire into `install_proxy` / `uninstall_proxy` for `agent=cursor`  
6. README + CHANGELOG; tests  

## Self-review gaps (and locks)

### P0 — must fix in implementation

**G1. `http.noProxy` for localhost (BYOK + local services)**  
Setting Cursor `http.proxy` routes *all* IDE HTTP through mitmdump. Requests to Memor BYOK (`127.0.0.1:8421`), dashboard, and other loopback services must bypass the proxy.

- **Lock:** Also write:
  - `http.proxyBypassList` / equivalent, **or** documented VS Code/Cursor keys:
    - Prefer `"http.noProxy": "127.0.0.1,localhost,::1"` if supported
    - Else `"http.proxyBypassList": "127.0.0.1, localhost, ::1"` (verify against Cursor/VS Code schema at impl time)
- Without this, full-stack install can break BYOK proxy even when wire works.

**G2. Health gate before leaving Cursor on the wire port**  
Same class of bug as pre-failover proxy install: writing `http.proxy` before mitmdump is proven healthy leaves Composer hung on a dead port.

- **Lock:** Order = start wire unit → TCP (or CONNECT) health probe up to ~15s → **then** write/keep Cursor wire keys.  
- If health fails: do **not** leave wire proxy keys active; keep/offer BYOK + Shell hooks; `cursor_wire=false`; print fix steps.  
- Mirror `failover_proxy_agents` with `failover_cursor_wire(reason)` that strips wire keys only (does not necessarily uninstall BYOK).

**G3. Failover when wire dies after install**  
`memor service stop` / crash / upgrade race: Cursor still has `http.proxy` → broken Composer.

- **Lock:** On `service stop` and `service uninstall`, strip or restore wire proxy keys (not only unload the unit).  
- On `service restart`, if wire fails health after restart → `failover_cursor_wire`.  
- Status output should show `cursor-wire: running|stopped|not installed`.

**G4. launchd PATH cannot see `mitmdump`**  
`memor cursor-wire-mitm` uses `shutil.which("mitmdump")`. launchd’s PATH is minimal; pipx-injected mitmdump may be invisible.

- **Lock:** Resolve absolute path to mitmdump at install time (same venv as `memor` binary, or `python -m mitmproxy.tools.dump`) and bake that into the plist/unit **or** have `cursor-wire-mitm` invoke mitmdump via `sys.executable -m …` from Memor’s interpreter. Never rely on login-shell PATH in the service.

**G5. Pre-existing user `http.proxy`**  
Corporate VPN / existing proxy settings get overwritten.

- **Lock:** Backup full settings (already). On install, if `http.proxy` is set and is **not** a Memor wire URL, warn prominently and require confirm (“Replace your existing proxy settings?”). Restore on uninstall. Do not silently clobber.

### P1 — should specify

**G6. Escape hatch for BYOK-only**  
Full MITM is a deal-breaker for some; locking “always full stack” fights earlier product guidance.

- **Lock:** Default = full stack. Add `--no-wire` to skip CA + mitmdump + wire settings (BYOK + Shell hooks + memory only).

**G7. Non-interactive / CI**  
Prompts fail without TTY.

- **Lock:** `--yes` accepts explain + CA prompts (CA trust still needs sudo/Touch ID unless `--skip-ca-trust` with explicit warning that wire will not work until trusted).

**G8. Wire port stolen after restart**  
`cursor_wire_port` persisted but another process binds it later → crash-loop.

- **Lock:** On service install/restart, if configured port busy and not already our mitmdump, re-pick in range, update config + Cursor `http.proxy`, then start. Log clearly.

**G9. CA already trusted**  
Second install should not fail on duplicate cert.

- **Lock:** Detect existing trust (best-effort); skip sudo with “CA already trusted”.

**P2 — document / defer**

**G10. Global proxy blast radius**  
Even with noProxy, Cursor update checks / non-cursor HTTPS still traverse mitmdump. Addon must fail-open (passthrough) for non-`*.cursor.sh` hosts — already true in addon host filter for *compression*, but TLS interception still occurs for proxied hosts. Document in README security section.

**G11. Linux CA / Windows**  
macOS first; Linux = documented manual trust; Windows out of scope until service layer supports it (same as launchd/systemd today).

**G12. Partial failure rollback**  
If settings write fails after CA trust, CA remains (harmless). If unit starts then settings fail → stop unit. If settings written then unit fails → failover wire keys (G2).

**G13. Shell hooks on uninstall**  
Leaving compress hooks installed is fine; document. Optional `--purge-hooks` later.

**G14. Memory hook install details**  
“If missing” = run existing `install-hook` for Claude (Cursor shares `~/.claude/settings.json`). Do not invent a separate Cursor hook installer.

**G15. mitmdump has no Memor `/health` JSON**  
Health = accept TCP on wire port after start, optionally `HTTP CONNECT` smoke test — not the proxy `:8421/health` endpoint. Same probe feeds install gating **and** dashboard Cursor Wire chip.

### Resolved contradictions

| Topic | Resolution |
|---|---|
| Two confirms vs “password once” | Two `[Y/n]` OK; only one privileged CA prompt |
| Backup files | Keep single `proxy-backup-cursor.json` snapshot before any Cursor writes; wire keys restored from that snapshot on uninstall/failover |
| Install order vs flow diagram | Flow diagram updated mentally: **health before committing wire keys** (see G2); diagram in “User-facing install flow” should be read with that override |

## Related

- Spike / Phase 1–2: `docs/plans/2026-08-02-cursor-wire-compression-design.md`  
- Proxy lifecycle patterns: `docs/plans/2026-08-01-proxy-lifecycle-failover-design.md`
