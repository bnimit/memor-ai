"""Tests for path-based agent resolution on the proxy."""
from __future__ import annotations

from memor.proxy.upstream import resolve_agent, resolve_agent_from_path


def test_resolve_agent_from_path_cursor():
    assert resolve_agent_from_path("/cursor/v1/chat/completions") == "cursor"
    assert resolve_agent_from_path("/cline/v1/messages") == "cline"
    assert resolve_agent_from_path("/opencode/v1/chat/completions") == "opencode"
    assert resolve_agent_from_path("/v1/chat/completions") is None


def test_resolve_agent_prefers_path_over_headers():
    agent = resolve_agent({"x-agent": "codex"}, path="/cursor/v1/chat/completions")
    assert agent == "cursor"


def test_resolve_agent_header_fallback():
    assert resolve_agent({"x-agent": "goose"}) == "goose"
    assert resolve_agent({}) == "unknown"


def test_resolve_agent_infers_claude_for_anthropic(tmp_path, monkeypatch):
    import memor.config as cfg

    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
    cfg.set_proxy_agent("claude", True)
    cfg.set_proxy_upstream(
        "claude",
        protocol="anthropic",
        base_url="https://api.anthropic.com/v1/messages",
        provider_name="anthropic",
    )
    assert resolve_agent({}, protocol="anthropic") == "claude"
