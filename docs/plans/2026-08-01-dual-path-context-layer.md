# Dual-Path Context Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local opt-in LLM proxy that compresses latest-turn tool payloads and ledgers token savings, while keeping hooks for shared cross-agent memory — with dashboard savings as the primary KPI.

**Architecture:** Dual-path local service: (A) existing hooks for memory on all agents; (B) new `127.0.0.1:8421` reverse proxy (Anthropic Messages + OpenAI Chat) that compresses request-side tool content, stores originals in CCR, optionally injects memory once, streams responses, and writes a savings ledger. Per-agent install flags make hooks skip inject for proxied Claude/Codex only. Cursor/Copilot always keep hooks.

**Tech Stack:** Python 3.11+, FastAPI/Starlette + httpx (already in deps), tiktoken (`memor.tokencount`), SQLite (`SqliteStore`), Typer CLI, pytest + FastAPI TestClient. No new LLM dependency. No mitmproxy.

**Spec:** `docs/plans/2026-08-01-dual-path-context-layer-design.md`

## Global Constraints

- No Memor-owned cloud/LLM; compressors are local only.
- Proxy binds `127.0.0.1` only; refuse `0.0.0.0`.
- Default ports: dashboard `8420`, proxy `8421`.
- Compress **latest-turn tool payloads only** (cache-safe); stream responses passthrough.
- No double inject: proxied Claude/Codex → hooks skip; Cursor/Copilot never skip.
- Forward auth headers; never persist API keys.
- CCR: 7-day TTL + 2 GB cap; preserve errors/stacks/paths even without retrieve.
- Release gate: ≥5 scripted tasks (≥3 tool-rich), mean ≥15% request-token savings on tool-rich subset, no task failures.
- Memory remains fire-and-forget; proxy is explicit `memor install-proxy`.

---

## File structure (locked)

| Path | Responsibility |
|------|----------------|
| `memor/config.py` | Read/write `~/.memor/config.json` (proxy agent flags, ports, CCR caps) |
| `memor/compress/__init__.py` | Public `compress_text(text, content_type) -> CompressResult` |
| `memor/compress/detect.py` | Content-type detection (log / json / search / text) |
| `memor/compress/logs.py` | Log compressor (keep errors/stacks) |
| `memor/compress/json_crush.py` | JSON array/object compressor |
| `memor/compress/search.py` | Search-result style compressor |
| `memor/compress/types.py` | `CompressResult` dataclass |
| `memor/proxy/adapters.py` | Normalize Anthropic/OpenAI → tool payloads; apply compressed text |
| `memor/proxy/pipeline.py` | Latest-turn extract → compress → CCR → rebuild request |
| `memor/proxy/server.py` | FastAPI app: `/v1/messages`, `/v1/chat/completions`, health |
| `memor/proxy/forward.py` | httpx upstream forward + SSE stream passthrough |
| `memor/proxy/memory.py` | One-shot memory inject into latest user turn |
| `memor/proxy/mcp_retrieve.py` | MCP stdio server exposing `memor_retrieve` |
| `memor/proxy/install.py` | Backup/restore agent config; set proxy flags |
| `memor/store/sqlite_store.py` | Tables: `proxy_savings`, `ccr_blobs`; query helpers |
| `memor/service.py` | Supervise proxy unit alongside daemon/dashboard |
| `memor/hook_server.py` | Per-agent skip when `proxy_agents.<agent>` |
| `memor/cli.py` | `proxy`, `install-proxy`, `uninstall-proxy` commands |
| `memor/dashboard/server.py` | `/api/savings-ledger`, `/api/proxy-status` |
| `memor/dashboard/static/index.html` | Savings hero + dual-path status |
| `tests/test_compress_*.py`, `tests/test_proxy_*.py`, … | TDD coverage |
| `memor/eval/proxy_benchmark.py` | Release-gate harness |

---

### Task 1: Config module (`~/.memor/config.json`)

**Files:**
- Create: `memor/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `CONFIG_PATH: Path` → `~/.memor/config.json`
  - `load_config() -> dict`
  - `save_config(cfg: dict) -> None`
  - `is_proxy_agent(agent: str) -> bool`
  - `set_proxy_agent(agent: str, enabled: bool) -> None`
  - `proxy_port() -> int` (default 8421)
  - `ccr_ttl_seconds() -> int` (default 7*86400)
  - `ccr_max_bytes() -> int` (default 2*1024**3)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from pathlib import Path
import memor.config as cfg

def test_proxy_agent_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
    assert cfg.is_proxy_agent("claude") is False
    cfg.set_proxy_agent("claude", True)
    assert cfg.is_proxy_agent("claude") is True
    assert cfg.is_proxy_agent("cursor") is False
    cfg.set_proxy_agent("claude", False)
    assert cfg.is_proxy_agent("claude") is False

def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    assert cfg.proxy_port() == 8421
    assert cfg.ccr_ttl_seconds() == 7 * 86400
    assert cfg.ccr_max_bytes() == 2 * 1024**3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'memor.config'`

- [ ] **Step 3: Write minimal implementation**

```python
# memor/config.py
from __future__ import annotations
import json
import os
from pathlib import Path

STATE_DIR = Path.home() / ".memor"
CONFIG_PATH = STATE_DIR / "config.json"

_DEFAULTS = {
    "proxy_agents": {},  # {"claude": true, "codex": true}
    "proxy_port": 8421,
    "ccr_ttl_seconds": 7 * 86400,
    "ccr_max_bytes": 2 * 1024**3,
}

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return json.loads(json.dumps(_DEFAULTS))
    data = json.loads(CONFIG_PATH.read_text())
    out = {**_DEFAULTS, **data}
    out["proxy_agents"] = {**_DEFAULTS["proxy_agents"], **data.get("proxy_agents", {})}
    return out

def save_config(cfg: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")

def is_proxy_agent(agent: str) -> bool:
    return bool(load_config().get("proxy_agents", {}).get(agent, False))

def set_proxy_agent(agent: str, enabled: bool) -> None:
    cfg = load_config()
    agents = dict(cfg.get("proxy_agents", {}))
    if enabled:
        agents[agent] = True
    else:
        agents.pop(agent, None)
    cfg["proxy_agents"] = agents
    save_config(cfg)

def proxy_port() -> int:
    try:
        return int(os.environ.get("MEMOR_PROXY_PORT", load_config().get("proxy_port", 8421)))
    except (TypeError, ValueError):
        return 8421

def ccr_ttl_seconds() -> int:
    return int(load_config().get("ccr_ttl_seconds", 7 * 86400))

def ccr_max_bytes() -> int:
    return int(load_config().get("ccr_max_bytes", 2 * 1024**3))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add memor/config.py tests/test_config.py
git commit -m "feat(config): add ~/.memor/config.json for proxy agent flags"
```

---

### Task 2: Content-type detect + compressors

**Files:**
- Create: `memor/compress/types.py`, `detect.py`, `logs.py`, `json_crush.py`, `search.py`, `__init__.py`
- Test: `tests/test_compress_detect.py`, `tests/test_compress_logs.py`, `tests/test_compress_json.py`

**Interfaces:**
- Produces:
  - `@dataclass CompressResult`: `text: str`, `content_type: str`, `tokens_before: int`, `tokens_after: int`, `passthrough: bool`, `ccr_id: str | None`
  - `detect_content_type(text: str) -> str`  # "log" | "json" | "search" | "text"
  - `compress_text(text: str, *, content_type: str | None = None) -> CompressResult`
  - On exception inside a compressor: return original text with `passthrough=True`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_compress_detect.py
from memor.compress.detect import detect_content_type

def test_detect_json():
    assert detect_content_type('[{"a":1},{"a":2}]') == "json"

def test_detect_log():
    sample = "2026-08-01 10:00:00 INFO ok\n" * 20 + "ERROR boom\nTraceback (most recent call last):\n  File x\n"
    assert detect_content_type(sample) == "log"

def test_detect_search():
    assert detect_content_type("path/to/file.py:12: matched line\nother.py:3: hit") == "search"

# tests/test_compress_logs.py
from memor.compress import compress_text

def test_log_keeps_error_drops_info_noise():
    lines = [f"2026-08-01 INFO fine {i}" for i in range(100)]
    lines.append("ERROR something failed")
    lines.append("Traceback (most recent call last):")
    lines.append('  File "app.py", line 1')
    text = "\n".join(lines)
    r = compress_text(text, content_type="log")
    assert r.passthrough is False
    assert "ERROR something failed" in r.text
    assert "Traceback" in r.text
    assert r.tokens_after < r.tokens_before

# tests/test_compress_json.py
from memor.compress import compress_text

def test_json_array_keeps_structure_shrinks():
    arr = [{"id": i, "ok": True, "blob": "x" * 50} for i in range(40)]
    import json
    text = json.dumps(arr)
    r = compress_text(text, content_type="json")
    assert r.tokens_after < r.tokens_before
    assert r.passthrough is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_compress_detect.py tests/test_compress_logs.py tests/test_compress_json.py -v`  
Expected: FAIL import errors

- [ ] **Step 3: Implement compressors**

Rules (must match spec):
- `logs.py`: keep lines matching `(?i)(error|fatal|traceback|exception|failed|CRITICAL)` and nearby context (±2 lines); keep first/last 5 lines; drop repetitive INFO/DEBUG middle.
- `json_crush.py`: if top-level array length > 10, keep first 3 + last 2 + any objects whose stringified form matches error-ish keys/values; emit compact JSON array ending with `{"_memor_note":"kept N of M items"}` (must remain parseable JSON).
- `search.py`: keep lines with `error`/`fail` case-insensitive; else top 20 lines by length uniqueness heuristic; cap 80 lines.
- `detect.py`: try `json.loads` → json; else if ≥3 lines match `^.+:\d+:` → search; else if ≥3 timestamp-like or log-level tokens → log; else text.
- `__init__.py`: `compress_text` uses `memor.tokencount.count_tokens`; wraps compressor in try/except → passthrough.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_compress_detect.py tests/test_compress_logs.py tests/test_compress_json.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add memor/compress tests/test_compress_detect.py tests/test_compress_logs.py tests/test_compress_json.py
git commit -m "feat(compress): add local log/json/search compressors"
```

---

### Task 3: Savings ledger + CCR tables

**Files:**
- Modify: `memor/store/sqlite_store.py` (`_init_schema`, new methods)
- Test: `tests/test_proxy_ledger.py`

**Interfaces:**
- Produces on `SqliteStore`:
  - `record_proxy_savings(row: dict) -> int`  # returns row id
  - `get_proxy_savings_summary(days: int = 30) -> dict`
  - `ccr_put(blob_id: str, text: str, content_type: str, created_at: float) -> None`
  - `ccr_get(blob_id: str) -> str | None`
  - `ccr_evict(ttl_seconds: int, max_bytes: int) -> int`  # evicted count

Schema:

```sql
CREATE TABLE IF NOT EXISTS proxy_savings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp REAL,
  agent TEXT,
  provider TEXT,
  session_id TEXT,
  tokens_before INTEGER,
  tokens_after INTEGER,
  content_types TEXT,  -- JSON map type->count
  passthrough INTEGER DEFAULT 0,
  upstream_input_tokens INTEGER,
  upstream_cache_read_tokens INTEGER,
  upstream_output_tokens INTEGER
);

CREATE TABLE IF NOT EXISTS ccr_blobs(
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  content_type TEXT,
  byte_len INTEGER,
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_ccr_created ON ccr_blobs(created_at);
```

- [ ] **Step 1: Write failing test**

```python
# tests/test_proxy_ledger.py
import time
from memor.store.sqlite_store import SqliteStore

def test_record_and_summary(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.record_proxy_savings({
        "timestamp": time.time(),
        "agent": "claude",
        "provider": "anthropic",
        "session_id": "s1",
        "tokens_before": 1000,
        "tokens_after": 400,
        "content_types": {"log": 1},
        "passthrough": 0,
    })
    summary = s.get_proxy_savings_summary(days=30)
    assert summary["tokens_before"] == 1000
    assert summary["tokens_after"] == 400
    assert summary["pct_saved"] == 60.0

def test_ccr_put_get_evict(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.ccr_put("b1", "FULL TEXT", "log", created_at=1.0)
    assert s.ccr_get("b1") == "FULL TEXT"
    n = s.ccr_evict(ttl_seconds=0, max_bytes=1)  # everything expired / over cap
    assert n >= 1
    assert s.ccr_get("b1") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_proxy_ledger.py -v`  
Expected: FAIL `record_proxy_savings` missing

- [ ] **Step 3: Implement schema + methods** in `sqlite_store.py` following existing `CREATE TABLE IF NOT EXISTS` + call from `_init_schema`. `pct_saved = (1 - after/before)*100` when before > 0 else 0.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_proxy_ledger.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add memor/store/sqlite_store.py tests/test_proxy_ledger.py
git commit -m "feat(store): add proxy_savings ledger and CCR blob tables"
```

---

### Task 4: Protocol adapters + compression pipeline

**Files:**
- Create: `memor/proxy/adapters.py`, `memor/proxy/pipeline.py`, `memor/proxy/__init__.py`
- Test: `tests/test_proxy_pipeline.py`

**Interfaces:**
- Produces:
  - `extract_latest_tool_payloads(provider: str, body: dict) -> list[ToolPayload]`
    - `ToolPayload(path: list, text: str)` where `path` locates the string in the JSON body
  - `apply_payload_text(body: dict, path: list, new_text: str) -> dict` (deep copy)
  - `run_pipeline(provider: str, body: dict, store: SqliteStore) -> PipelineResult`
    - `PipelineResult(body: dict, tokens_before: int, tokens_after: int, content_types: dict, passthrough: bool, ccr_ids: list[str])`

Anthropic latest-turn tool payloads: in the **last** message with `role=="user"`, content blocks with `type=="tool_result"` and string `content` (or list of text parts joined).

OpenAI: trailing messages with `role=="tool"` and string `content`, contiguous suffix before the next user/assistant boundary — treat the contiguous trailing tool-message run as “latest turn”.

- [ ] **Step 1: Write failing test**

```python
# tests/test_proxy_pipeline.py
from memor.proxy.pipeline import run_pipeline
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder

def test_anthropic_compresses_only_latest_tool_result(tmp_path):
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    old_log = "INFO old\n" * 50
    new_log = "\n".join([f"INFO noise {i}" for i in range(80)] + ["ERROR boom", "Traceback (most recent call last):"])
    body = {
        "model": "claude-sonnet-4-0",
        "messages": [
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": old_log}]},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "2", "content": new_log}]},
        ],
    }
    result = run_pipeline("anthropic", body, store)
    # First tool_result unchanged
    assert result.body["messages"][0]["content"][0]["content"] == old_log
    # Latest compressed
    latest = result.body["messages"][2]["content"][0]["content"]
    assert latest != new_log
    assert "ERROR boom" in latest
    assert result.tokens_after < result.tokens_before
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_proxy_pipeline.py -v`

- [ ] **Step 3: Implement adapters + pipeline**  
  On compressor failure for one payload: leave that payload unchanged, set overall `passthrough` only if **all** failed or none compressed. Call `store.ccr_put` with uuid4 hex before replacing text; prepend a short marker line to compressed text: `[memor:ccr:<id>]` so retrieve can find it.

- [ ] **Step 4: Run to verify pass**

- [ ] **Step 5: Commit**

```bash
git add memor/proxy tests/test_proxy_pipeline.py
git commit -m "feat(proxy): latest-turn tool payload compression pipeline"
```

---

### Task 5: HTTP proxy server (Anthropic) + stream forward

**Files:**
- Create: `memor/proxy/forward.py`, `memor/proxy/server.py`
- Modify: `memor/cli.py` (add `proxy` command)
- Test: `tests/test_proxy_server.py`

**Interfaces:**
- Produces:
  - `create_proxy_app(db_path: str | None = None) -> FastAPI`
  - Routes:
    - `GET /health` → `{"ok": true, "bind": "127.0.0.1"}`
    - `POST /v1/messages` → Anthropic Messages upstream `https://api.anthropic.com/v1/messages`
  - `forward_stream(method, url, headers, content) -> StreamingResponse`
  - CLI: `memor proxy --port 8421` binds **only** `127.0.0.1`

Header forwarding: copy incoming headers except `host`; never log Authorization values.

Bind guard:

```python
def _assert_localhost(host: str) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"refusing non-localhost bind: {host}")
```

- [ ] **Step 1: Write failing tests**

```python
# tests/test_proxy_server.py
from fastapi.testclient import TestClient
from memor.proxy.server import create_proxy_app
import httpx
import respx  # if not available, use monkeypatch on forward module

def test_health(tmp_path):
    app = create_proxy_app(str(tmp_path / "m.db"))
    c = TestClient(app)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True

def test_messages_runs_pipeline_and_forwards(tmp_path, monkeypatch):
    from memor.proxy import forward as fwd
    async def fake_forward(*, method, url, headers, content, stream):
        assert "api.anthropic.com" in url
        assert "x-api-key" in {k.lower() for k in headers}
        # return non-stream JSON response shape
        class R:
            status_code = 200
            headers = {"content-type": "application/json"}
            async def aiter_bytes(self):
                yield b'{"content":[{"type":"text","text":"hi"}],"usage":{"input_tokens":10,"output_tokens":2}}'
            async def aread(self):
                return b'{"content":[{"type":"text","text":"hi"}],"usage":{"input_tokens":10,"output_tokens":2}}'
        return R()
    monkeypatch.setattr(fwd, "forward_request", fake_forward)
    app = create_proxy_app(str(tmp_path / "m.db"))
    c = TestClient(app)
    body = {
        "model": "claude-sonnet-4-0",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
    }
    r = c.post("/v1/messages", json=body, headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"})
    assert r.status_code == 200
```

Note: if `respx` is not a dependency, **do not add it** — monkeypatch `forward_request` only.

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement server + forward**  
  - Use `httpx.AsyncClient` with `timeout=None` for streams.  
  - If `stream=true` in body or `accept: text/event-stream`, pipe `aiter_bytes` to client.  
  - After building modified body via `run_pipeline`, `record_proxy_savings`.  
  - Parse upstream usage JSON when non-stream to fill ledger upstream_* fields.  
  - CLI entry starts uvicorn with `host="127.0.0.1"`.

- [ ] **Step 4: Run to verify pass**

- [ ] **Step 5: Commit**

```bash
git add memor/proxy/server.py memor/proxy/forward.py memor/cli.py tests/test_proxy_server.py
git commit -m "feat(proxy): Anthropic /v1/messages localhost reverse proxy"
```

---

### Task 6: OpenAI Chat Completions path

**Files:**
- Modify: `memor/proxy/server.py`, `memor/proxy/adapters.py`
- Test: `tests/test_proxy_openai.py`

**Interfaces:**
- Produces: `POST /v1/chat/completions` → `https://api.openai.com/v1/chat/completions`
- Reuses `run_pipeline("openai", body, store)`

- [ ] **Step 1: Failing test** — same pattern as Task 5 with OpenAI messages (`role: tool` trailing) and assert upstream URL contains `api.openai.com`.

- [ ] **Step 2: Run to fail**

- [ ] **Step 3: Implement route + openai extract/apply in adapters**

- [ ] **Step 4: Run to pass**

- [ ] **Step 5: Commit**

```bash
git add memor/proxy tests/test_proxy_openai.py
git commit -m "feat(proxy): OpenAI /v1/chat/completions path"
```

---

### Task 7: Proxy memory inject + hook skip

**Files:**
- Create: `memor/proxy/memory.py`
- Modify: `memor/hook_server.py`, `memor/proxy/server.py`
- Test: `tests/test_proxy_memory_and_skip.py`

**Interfaces:**
- Produces:
  - `inject_memory(provider: str, body: dict, *, project: str, db_path: str) -> dict`
  - Hook: after `detect_agent`, if `is_proxy_agent(agent)` and agent in `{"claude","codex"}`: return skipped response `"Memor: skipped — proxy path active"` with status logged `skipped_proxy` (no recall).

- [ ] **Step 1: Write failing tests**

```python
def test_hook_skips_when_claude_proxied(tmp_path, monkeypatch):
    import memor.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
    cfg.set_proxy_agent("claude", True)
    from memor.hook_server import handle_request
    # minimal claude-like req; use FakeEmbedder path via existing test helpers
    ...

def test_hook_cursor_still_recalls_when_claude_proxied(tmp_path, monkeypatch):
    ...

def test_proxy_injects_recalled_memories_markdown(tmp_path):
    # seed store with memory, call inject_memory on anthropic body, assert "## Recalled Memories" in latest user text
    ...
```

Fill tests using patterns from `tests/test_hook_server.py` (copy request fixtures).

- [ ] **Step 2: Run to fail**

- [ ] **Step 3: Implement**  
  - `inject_memory` calls existing `recall()` with project from cwd/header `x-memor-project` or `unknown`.  
  - Append markdown to latest user message text/content.  
  - Wire into proxy after pipeline, before forward.

- [ ] **Step 4: Run to pass**

- [ ] **Step 5: Commit**

```bash
git add memor/proxy/memory.py memor/hook_server.py memor/proxy/server.py tests/test_proxy_memory_and_skip.py
git commit -m "feat(proxy): one-shot memory inject and per-agent hook skip"
```

---

### Task 8: Service supervision + install/uninstall CLI

**Files:**
- Modify: `memor/service.py`, `memor/cli.py`
- Create: `memor/proxy/install.py`
- Test: `tests/test_proxy_install.py`, `tests/test_service_proxy_unit.py`

**Interfaces:**
- Service unit key `proxy`, label `ai.memor.proxy`, args `memor proxy --port <port>`, log `~/.memor/proxy.log`
- `_all_unit_labels` includes proxy
- `memor/proxy/install.py`:
  - `backup_agent_config(agent: str) -> Path` → `~/.memor/proxy-backup-<agent>.json`
  - `install_claude_proxy(port: int) -> None` — set Anthropic base URL for Claude Code to `http://127.0.0.1:<port}` (write fields Claude Code actually reads; probe `~/.claude/settings.json` for existing `env` / API base keys used in the wild: `ANTHROPIC_BASE_URL`)
  - `install_codex_proxy(port: int) -> None` — set `OPENAI_BASE_URL=http://127.0.0.1:<port>/v1` in Codex config env
  - `uninstall_agent_proxy(agent: str) -> None` — restore backup; `set_proxy_agent(agent, False)`
- CLI:
  - `memor install-proxy --agent claude|codex`
  - `memor uninstall-proxy --agent claude|codex`
  - Both call `service.install()` / ensure proxy unit running after install

Claude Code wiring (v1): merge into `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8421"
  }
}
```

Codex: merge into `~/.codex/config.toml` or hooks env file — **read current Codex config format in repo/docs before coding**; if TOML, use a minimal parser/`tomllib` update of `[env]` / project config. If uncertain, store env in `~/.codex/.env` only if Codex loads it; prefer documented Codex `model_provider` base_url override.

- [ ] **Step 1: Failing tests** for backup/restore roundtrip using `tmp_path` as fake home (`monkeypatch.setenv("HOME", ...)`) and service unit list contains proxy.

- [ ] **Step 2: Run to fail**

- [ ] **Step 3: Implement install helpers + service unit + CLI**

- [ ] **Step 4: Run to pass**

- [ ] **Step 5: Commit**

```bash
git add memor/service.py memor/cli.py memor/proxy/install.py tests/test_proxy_install.py tests/test_service_proxy_unit.py
git commit -m "feat(proxy): install/uninstall CLI and service unit"
```

---

### Task 9: Dashboard savings hero + dual-path status

**Files:**
- Modify: `memor/dashboard/server.py`, `memor/dashboard/static/index.html`
- Test: `tests/test_dashboard_proxy.py`

**Interfaces:**
- `GET /api/savings-ledger?days=30` → store summary + per-day series + content_types
- `GET /api/proxy-status` → `{proxy: bool, hook: bool, daemon: bool, proxy_agents: {...}}`  
  Health: TCP connect `127.0.0.1:8421/health`; hook via `~/.memor/hook.sock` exists; daemon via launchctl/systemd or pid heuristics already used in `service.status` — reuse simplest: check log mtime or call into `service.status` parsing.

UI: top hero shows **% tokens saved** and before/after; status pills; demote old recall hero visually (CSS order / smaller).

- [ ] **Step 1: Failing API tests with TestClient + seeded `proxy_savings`**

- [ ] **Step 2: Run to fail**

- [ ] **Step 3: Implement endpoints + HTML fetch/render**

- [ ] **Step 4: Run to pass** + manual glance that HTML contains `savings-ledger`

- [ ] **Step 5: Commit**

```bash
git add memor/dashboard tests/test_dashboard_proxy.py
git commit -m "feat(dashboard): proxy savings hero and dual-path status"
```

---

### Task 10: MCP `memor_retrieve` + CCR wiring

**Files:**
- Create: `memor/proxy/mcp_retrieve.py`
- Modify: `memor/proxy/install.py` (register MCP), `pyproject.toml` scripts if needed
- Test: `tests/test_mcp_retrieve.py`

**Interfaces:**
- Produces stdio MCP server tool:
  - `memor_retrieve(id: str) -> str` reads `ccr_get`; on miss returns `"memor: CCR miss for <id> (expired or unknown)"`
- Entry: `memor-retrieve-mcp` console script → `memor.proxy.mcp_retrieve:main`
- Install registers in Claude/Codex MCP config pointing at that binary.

Use the MCP Python SDK **only if already a dependency**; otherwise implement minimal JSON-RPC stdio MCP subset for `tools/list` + `tools/call` (keep under ~150 LOC) — YAGNI: do not add heavy deps without need.

- [ ] **Step 1: Failing test** calling the tool function directly (not full stdio):

```python
def test_retrieve_hit_and_miss(tmp_path):
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    store.ccr_put("abc", "SECRET FULL", "log", created_at=time.time())
    from memor.proxy.mcp_retrieve import retrieve
    assert retrieve("abc", store) == "SECRET FULL"
    assert "CCR miss" in retrieve("nope", store)
```

- [ ] **Step 2–4: Implement + pass**

- [ ] **Step 5: Commit**

```bash
git add memor/proxy/mcp_retrieve.py memor/proxy/install.py pyproject.toml tests/test_mcp_retrieve.py
git commit -m "feat(proxy): memor_retrieve MCP for CCR originals"
```

---

### Task 11: Proxy benchmark harness (release gate)

**Files:**
- Create: `memor/eval/proxy_benchmark.py`, `tests/fixtures/proxy_benchmark/README.md`
- Create: `tests/test_proxy_benchmark_unit.py` (unit-level: compression savings on fixture payloads, not live API)
- Modify: `memor/cli.py` → `memor eval-proxy` optional

**Interfaces:**
- `run_benchmark(fixtures_dir: Path) -> BenchmarkReport` with `tool_rich_mean_pct_saved`, `tasks: list[{name, pct_saved, passed}]`
- Exit code 0 only if ≥3 tool-rich fixtures each save >0 and mean ≥15%, and all `passed` (fixture self-check: compressed text still contains required substrings like `ERROR`).

Ship ≥5 fixture JSON request bodies under `tests/fixtures/proxy_benchmark/` (3 tool-rich logs/json).

- [ ] **Step 1: Write fixtures + failing test that report.mean ≥ 15 on fixtures**

- [ ] **Step 2–4: Implement harness offline (no network)**

- [ ] **Step 5: Commit**

```bash
git add memor/eval/proxy_benchmark.py tests/fixtures/proxy_benchmark tests/test_proxy_benchmark_unit.py memor/cli.py
git commit -m "feat(eval): offline proxy savings benchmark for release gate"
```

---

### Task 12: README + product messaging

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README** sections:
  - Positioning: measured memory **and** opt-in local token savings
  - Dual-path diagram (hooks vs proxy)
  - `memor install-proxy --agent claude` quickstart
  - Explicit: memory = fire-and-forget; proxy = opt-in; no Memor API key
  - Dashboard savings hero mention
  - Agent matrix table from spec

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: dual-path proxy + memory positioning in README"
```

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| Local proxy Anthropic + OpenAI | 5, 6 |
| Compress latest-turn tools only | 4 |
| Local compressors, preserve errors | 2 |
| CCR + TTL/2GB + retrieve MCP | 3, 10 |
| Stream passthrough | 5 |
| Ledger + dashboard primary KPI | 3, 9 |
| Hook skip per-agent; Cursor never | 7 |
| install/uninstall + backup | 8 |
| Service supervises proxy | 8 |
| Localhost only | 5 |
| No Memor API key | Global + all tasks |
| Release gate ≥15% / ≥5 tasks | 11 |
| README dual-path messaging | 12 |
| No double inject | 7 |
| Memory inject once on proxy | 7 |

**Placeholder scan:** none intentional. Codex exact config file format must be verified in Task 8 against installed Codex docs — step calls that out explicitly.

**Type consistency:** `CompressResult`, `PipelineResult`, `record_proxy_savings`, `is_proxy_agent` names are stable across tasks.

---

## Execution handoff

Plan complete and saved to `docs/plans/2026-08-01-dual-path-context-layer.md` (also copied under `docs/superpowers/plans/` if present).

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — execute tasks in this session with checkpoints  

Which approach?
