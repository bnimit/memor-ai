"""Per-agent upstream URL resolution for the proxy."""
from __future__ import annotations

from collections.abc import Mapping

from memor.config import get_proxy_upstream, load_config

_ANTHROPIC_DEFAULT = "https://api.anthropic.com/v1/messages"
_OPENAI_DEFAULT = "https://api.openai.com/v1/chat/completions"


def _header_get(headers: Mapping[str, str], name: str) -> str | None:
    lower = name.lower()
    for key, value in headers.items():
        if key.lower() == lower:
            return value
    getter = getattr(headers, "get", None)
    if getter is not None:
        return getter(name)
    return None


def resolve_agent(headers: Mapping[str, str]) -> str:
    """Resolve agent from request headers: x-agent, then agent, else unknown."""
    return _header_get(headers, "x-agent") or _header_get(headers, "agent") or "unknown"


def resolve_upstream_url(agent: str, protocol: str) -> str | None:
    """Resolve upstream URL for agent and API protocol."""
    upstream = get_proxy_upstream(agent)
    if upstream and upstream.get("protocol") == protocol:
        return upstream.get("base_url")

    all_upstreams = load_config().get("proxy_upstreams", {})
    matching = [
        entry["base_url"]
        for entry in all_upstreams.values()
        if entry.get("protocol") == protocol and entry.get("base_url")
    ]
    if len(matching) == 1:
        return matching[0]

    if agent == "claude" and protocol == "anthropic":
        return _ANTHROPIC_DEFAULT
    if agent == "codex" and protocol == "openai":
        return _OPENAI_DEFAULT

    return None
