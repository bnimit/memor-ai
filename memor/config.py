from __future__ import annotations
import json
import os
from pathlib import Path

STATE_DIR = Path.home() / ".memor"
CONFIG_PATH = STATE_DIR / "config.json"

_DEFAULTS = {
    "proxy_agents": {},  # {"claude": true, "codex": true}
    "proxy_upstreams": {},
    "proxy_port": 8421,
    "cursor_wire": False,
    "cursor_wire_port": 8080,
    "ccr_ttl_seconds": 7 * 86400,
    "ccr_max_bytes": 2 * 1024**3,
}

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return json.loads(json.dumps(_DEFAULTS))
    data = json.loads(CONFIG_PATH.read_text())
    out = {**_DEFAULTS, **data}
    out["proxy_agents"] = {**_DEFAULTS["proxy_agents"], **data.get("proxy_agents", {})}
    out["proxy_upstreams"] = {**_DEFAULTS["proxy_upstreams"], **data.get("proxy_upstreams", {})}
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

def get_proxy_upstream(agent: str) -> dict | None:
    """Return upstream dict or None. Dict keys: protocol, base_url, provider_name."""
    upstream = load_config().get("proxy_upstreams", {}).get(agent)
    return dict(upstream) if upstream else None

def set_proxy_upstream(agent: str, *, protocol: str, base_url: str, provider_name: str = "") -> None:
    """Persist upstream for agent under proxy_upstreams in config.json."""
    cfg = load_config()
    upstreams = dict(cfg.get("proxy_upstreams", {}))
    upstreams[agent] = {
        "protocol": protocol,
        "base_url": base_url,
        "provider_name": provider_name,
    }
    cfg["proxy_upstreams"] = upstreams
    save_config(cfg)

def clear_proxy_upstream(agent: str) -> None:
    """Remove agent entry from proxy_upstreams."""
    cfg = load_config()
    upstreams = dict(cfg.get("proxy_upstreams", {}))
    upstreams.pop(agent, None)
    cfg["proxy_upstreams"] = upstreams
    save_config(cfg)


def is_cursor_wire_enabled() -> bool:
    return bool(load_config().get("cursor_wire", False))


def set_cursor_wire(enabled: bool) -> None:
    cfg = load_config()
    cfg["cursor_wire"] = bool(enabled)
    save_config(cfg)


def cursor_wire_port() -> int:
    try:
        env = os.environ.get("MEMOR_CURSOR_WIRE_PORT")
        if env is not None and str(env).strip():
            return int(env)
        return int(load_config().get("cursor_wire_port", 8080))
    except (TypeError, ValueError):
        return 8080


def set_cursor_wire_port(port: int) -> None:
    cfg = load_config()
    cfg["cursor_wire_port"] = int(port)
    save_config(cfg)
