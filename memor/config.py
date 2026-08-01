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
