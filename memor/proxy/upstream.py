"""Per-agent upstream URL resolution for the proxy."""
from __future__ import annotations

import re
from collections.abc import Mapping

from memor.config import get_proxy_upstream, load_config

_PATH_AGENT_RE = re.compile(r"^/(?:agents/)?(cursor|cline|opencode)/v1/")

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


def resolve_agent_from_path(path: str) -> str | None:
    """Resolve agent from URL path prefix (/cursor/v1/..., /cline/v1/..., …)."""
    match = _PATH_AGENT_RE.match(path or "")
    return match.group(1) if match else None


def _agent_protocol(agent: str, upstreams: dict) -> str | None:
    """Return API protocol for a proxied agent (from config or legacy defaults)."""
    entry = upstreams.get(agent) or {}
    protocol = entry.get("protocol")
    if protocol:
        return protocol
    if agent == "claude":
        return "anthropic"
    if agent == "codex":
        return "openai"
    return None


def infer_agent_from_config(protocol: str) -> str | None:
    """Infer agent when headers are missing and exactly one proxied agent matches."""
    config = load_config()
    proxy_agents = config.get("proxy_agents", {})
    upstreams = config.get("proxy_upstreams", {})
    candidates = [
        agent
        for agent, enabled in proxy_agents.items()
        if enabled and _agent_protocol(agent, upstreams) == protocol
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def resolve_agent(
    headers: Mapping[str, str],
    *,
    path: str = "",
    protocol: str | None = None,
) -> str:
    """Resolve agent from path prefix, headers, config inference, else unknown."""
    from_path = resolve_agent_from_path(path)
    if from_path:
        return from_path
    header_agent = _header_get(headers, "x-agent") or _header_get(headers, "agent")
    if header_agent:
        return header_agent
    if protocol:
        inferred = infer_agent_from_config(protocol)
        if inferred:
            return inferred
    return "unknown"


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
