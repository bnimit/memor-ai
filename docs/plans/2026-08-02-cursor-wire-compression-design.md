# Cursor Wire Compression (Subscription Composer)

**Date:** 2026-08-02  
**Status:** Phase 2 implemented (decode + compress rewrite + dashboard attribution)

## Goal

Compress subscription Composer context on the wire — same outcome as Memor proxy for Claude/Codex — by intercepting `api2.cursor.sh` traffic, decoding `BidiAppend` payloads, shrinking tool/file strings, and re-encoding before forward.

## What we know (from capture + prior art)

| Piece | Detail |
|---|---|
| Host | `api2.cursor.sh` (HTTP/1.1 when `http.proxy` + `disableHttp2`) |
| Read channel | `agent.v1.AgentService/RunSSE` — long-lived SSE stream of model output |
| Write channel | `aiserver.v1.BidiService/BidiAppend` — user messages, tool results, heartbeats |
| Outer envelope | `BidiAppendRequest`: `data` (hex-encoded `AgentClientMessage`), `request_id`, `append_seqno` |
| Body encoding | Often **gzip-compressed** protobuf (`application/proto`) |
| Inner messages | `AgentClientMessage` oneof: `run_request`, `exec_client_message`, `conversation_action`, `client_heartbeat`, … |
| Compressible content | JSON chat blobs with `role: tool` / `user` — file reads (`1\|line`), shell output, search hits |

Prior art: [cursor-tap](https://github.com/burpheart/cursor-tap), [cursor reverse notes](https://rce.moe/2026/01/31/cursor-reverse-notes-1/).

**Key insight:** Format is proprietary but **not encrypted**. Hex wrapper + nested protobuf + JSON strings — same class of problem Squeezr/cursor-tap solve.

## Architecture (recommended)

```
Cursor IDE
    │  HTTPS (proxy + disableHttp2)
    ▼
memor-cursor-mitm (mitmproxy addon)
    ├─ passthrough by default (zero blocking)
    ├─ on BidiAppend request:
    │     gunzip → parse BidiAppendRequest
    │     hex-decode → AgentClientMessage
    │     walk JSON strings (tool results, file context)
    │     compress via memor.compress.compress_text
    │     re-encode hex → gzip → forward
    └─ on RunSSE: passthrough (read-only stream; no compression target)
    ▼
api2.cursor.sh
```

### Phase 1 — Decode spike (this week)

- [x] Generic protobuf string walker (no `.proto` stubs)
- [x] `BidiAppendRequest` + hex `AgentClientMessage` decoder
- [x] mitm script saves full payloads to `grpc_logs/payloads/`
- [ ] Re-capture with mitm running; confirm JSON tool blobs in 20KB frames
- [ ] Optional: vendor `agent_v1.proto` / `aiserver_v1.proto` from cursor-tap for typed decode

### Phase 2 — Compress rewrite

- [x] Identify compress targets inside `exec_client_message` / `run_request` string fields
- [x] Apply `memor.compress` (plus plain-text head/tail trim for large file reads)
- [x] Round-trip encode: modified strings → protobuf → hex → `BidiAppendRequest` → gzip
- [x] Ledger rows in Memor DB (`agent=cursor-wire`, provider=`cursor-bidi`)
- [x] Dashboard: Proxy savings by agent shows **Cursor Wire**
- [x] CLI: `memor cursor-wire-mitm` (requires `pip install 'memor-cli[cursor-wire]'`)

### Phase 3 — Product integration

**Locked spec:** [`2026-08-02-cursor-full-install-automation-design.md`](./2026-08-02-cursor-full-install-automation-design.md)

- Extend `memor install-proxy --agent cursor` → full Cursor stack (hooks, Shell compress, BYOK, wire mitmdump, CA trust, settings)
- Wire unit lifecycle tied to `memor service` restart/stop/uninstall
- Port auto-pick when 8080 busy; sync into Cursor `http.proxy` + config
- Document coexistence with Shell hooks (hooks = pre-assembly; wire = post-assembly)

## Approaches considered

| Approach | Pros | Cons |
|---|---|---|
| **A. mitmproxy addon + proto walk** (recommended) | Reuses capture setup; no Cursor binary patch | Requires proxy settings; must handle gzip+hex round-trip |
| B. Fork cursor-tap (Go) | Full proto extraction + WebUI | Second stack; harder to reuse Memor compressors |
| C. Hosts redirect to Memor :8421 | Single port with other agents | Cursor subscription doesn't use custom base URL today |
| D. Shell hooks only | Shipped in PR #44 | Cannot compress Read/MCP/file context |

## Risks

1. **Round-trip fidelity** — bad re-encode breaks Composer; need golden tests on captured payloads
2. **Schema drift** — Cursor updates change field numbers; proto walk is more resilient than typed stubs
3. **Proxy requirement** — subscription path without proxy may use HTTP/2 direct (harder MITM)
4. **Heartbeats** — 48-byte `client_heartbeat` frames must pass through untouched
5. **Legal/ToS** — wire modification is user-local research; document clearly

## Success criteria

1. Decode a captured 20KB `BidiAppend` and print at least one `role: tool` JSON blob with file content
2. Compress that blob, re-encode, forward through mitm — Composer turn still succeeds
3. Measurable token reduction on dashboard for `cursor-wire` agent

## Next action

Re-run mitm capture with updated script, then:

```bash
cd ~/Documents/Projects/cursor-aiserver-interceptor
.venv/bin/mitmweb -s ./mitm_cursor_all_hosts.py --mode regular@8080 --web-port 8081 --set block_global=false
# Cursor settings: http.proxy, disableHttp2, proxyStrictSSL false
# Send one Composer prompt, then:
.venv/bin/python scripts/decode_bidi_capture.py grpc_logs/payloads/*BidiAppend*.bin --dump-strings
```
