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
    "ccr_ttl_seconds": 7 * 86400,
    "ccr_max_bytes": 2 * 1024**3,
    # Compress tool payloads the agent has moved past, not just the newest one.
    # Off until measured on real traffic — it changes what the model sees.
    "compress_older_turns": False,
    # Folders of notes the daemon ingests on every poll, like a session store.
    # A memory layer that needs to be told about a file by hand is not a memory
    # layer, so documents are watched rather than imported: point this at the
    # directories where decisions live outside any repo (an obsidian vault, a
    # docs/ tree, an incident log) and they stay in step with the files.
    "document_dirs": [],
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


def is_compress_older_turns() -> bool:
    """Whether to compress payloads the agent has already moved past.

    ``MEMOR_COMPRESS_OLDER`` overrides config so a run can be flipped without
    editing state — useful for A/B measurement.
    """
    env = os.environ.get("MEMOR_COMPRESS_OLDER")
    if env is not None and env.strip():
        return env.strip().lower() in {"1", "true", "yes", "on"}
    return bool(load_config().get("compress_older_turns", False))


def is_compress_source() -> bool:
    """Whether to skeletonize file contents the agent has already moved past.

    Source is 46.2% of tool-payload mass and `compress_text` has never touched
    it, which is the single largest reason realized compression sits at 7.3%.
    The reason it was withheld is real: an agent editing against elided lines
    corrupts code. Two findings make it safe to enable deliberately.

    The newest read of each file stays byte-exact, and that is the copy agents
    actually edit — of 3,664 real edits in local transcripts, 3,660 targeted a
    file whose read at edit time was the latest one.

    The original remains retrievable. A live model, shown a skeleton and asked
    for a value only present in an omitted body, called ``memor_retrieve`` and
    answered correctly; 300 of 300 real source payloads round-tripped
    byte-exact.

    ``MEMOR_COMPRESS_SOURCE`` overrides config so it can be flipped per run.
    """
    env = os.environ.get("MEMOR_COMPRESS_SOURCE")
    if env is not None and env.strip():
        return env.strip().lower() in {"1", "true", "yes", "on"}
    return bool(load_config().get("compress_source", False))


def set_compress_older_turns(enabled: bool) -> None:
    """Flip the flag and stamp when, so the change has a measurable boundary.

    Without a recorded instant there is nothing to compare before and after
    against, and the whole point of shipping this off-by-default is that it has
    to earn its default from data.
    """
    import time

    cfg = load_config()
    cfg["compress_older_turns"] = bool(enabled)
    if enabled:
        # Always re-stamp. Keeping an older value would date the boundary to a
        # run that has since ended — and a cost comparison split at a moment
        # when the current build was not yet deployed is confidently wrong.
        cfg["compress_started_at"] = time.time()
    else:
        cfg.pop("compress_started_at", None)
    save_config(cfg)


def clear_cursor_wire_keys() -> bool:
    """Drop legacy ``cursor_wire`` keys left by the removed MITM feature.

    Returns True if anything was removed. Kept so upgrading users do not carry
    dead config forward; safe to delete once no installs predate the removal.
    """
    cfg = load_config()
    removed = [k for k in ("cursor_wire", "cursor_wire_port") if k in cfg]
    if not removed:
        return False
    for key in removed:
        cfg.pop(key, None)
    save_config(cfg)
    return True
