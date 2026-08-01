"""Tests for proxy config failover."""
from __future__ import annotations

import json
import pytest
import memor.config
from memor.proxy.install import failover_proxy_agents, install_claude_proxy
from memor.config import is_proxy_agent, set_proxy_agent


@pytest.fixture
def mock_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(memor.config, "STATE_DIR", tmp_path / ".memor")
    monkeypatch.setattr(memor.config, "CONFIG_PATH", tmp_path / ".memor" / "config.json")
    return tmp_path


def test_failover_restores_backup_and_clears_flag(mock_home, monkeypatch):
    monkeypatch.setattr(
        "memor.proxy.install.shutil.which",
        lambda name: "/bin/memor-retrieve-mcp",
    )
    (mock_home / ".claude").mkdir()
    settings = mock_home / ".claude" / "settings.json"
    settings.write_text(json.dumps({"env": {"KEEP": "1"}}, indent=2) + "\n")
    install_claude_proxy(8421)
    assert is_proxy_agent("claude")
    assert "ANTHROPIC_BASE_URL" in json.loads(settings.read_text())["env"]

    lines = failover_proxy_agents("test reason")
    assert any("restored" in ln for ln in lines)
    assert not is_proxy_agent("claude")
    assert not (mock_home / ".memor" / "proxy-backup-claude.json").exists()
    data = json.loads(settings.read_text())
    assert data.get("env", {}).get("KEEP") == "1"
    assert "ANTHROPIC_BASE_URL" not in data.get("env", {})


def test_failover_strips_url_without_backup(mock_home):
    (mock_home / ".claude").mkdir()
    settings = mock_home / ".claude" / "settings.json"
    settings.write_text(json.dumps({
        "env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8421", "OTHER": "x"},
    }, indent=2) + "\n")
    set_proxy_agent("claude", True)

    lines = failover_proxy_agents()
    assert any("removed ANTHROPIC_BASE_URL" in ln for ln in lines)
    assert not is_proxy_agent("claude")
    env = json.loads(settings.read_text()).get("env", {})
    assert "ANTHROPIC_BASE_URL" not in env
    assert env.get("OTHER") == "x"


def test_failover_continues_after_per_agent_error(mock_home):
    """Per-agent I/O/JSON errors must not abort the loop or leave flags set."""
    (mock_home / ".claude").mkdir()
    (mock_home / ".claude" / "settings.json").write_text("{not-json")
    (mock_home / ".codex").mkdir()
    (mock_home / ".codex" / "config.toml").write_text(
        'openai_base_url = "http://127.0.0.1:8421/v1"\n'
    )
    set_proxy_agent("claude", True)
    set_proxy_agent("codex", True)

    lines = failover_proxy_agents("multi")
    assert any("claude" in ln and "failover error" in ln for ln in lines)
    assert any("codex" in ln and "removed openai_base_url" in ln for ln in lines)
    assert not is_proxy_agent("claude")
    assert not is_proxy_agent("codex")
