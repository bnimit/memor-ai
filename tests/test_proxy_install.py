"""Tests for proxy install/uninstall functionality using monkeypatched HOME."""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from unittest.mock import patch
from memor.proxy.install import (
    AGENT_PROXY_HANDLERS,
    backup_agent_config,
    install_agent_proxy,
    install_claude_proxy,
    install_codex_proxy,
    uninstall_agent_proxy,
)
from memor.config import get_proxy_upstream, is_proxy_agent, set_proxy_agent
import memor.config


@pytest.fixture
def mock_home(monkeypatch, tmp_path):
    """Monkeypatch HOME and config paths to use tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Also monkeypatch the config module's paths
    monkeypatch.setattr(memor.config, "STATE_DIR", tmp_path / ".memor")
    monkeypatch.setattr(memor.config, "CONFIG_PATH", tmp_path / ".memor" / "config.json")
    return tmp_path


def test_backup_agent_config(mock_home):
    """Backup creates ~/.memor/proxy-backup-<agent>.json."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    
    # Create a fake Claude config
    claude_dir = mock_home / ".claude"
    claude_dir.mkdir()
    settings = claude_dir / "settings.json"
    settings.write_text(json.dumps({"some": "config"}, indent=2))
    
    backup_path = backup_agent_config("claude")
    assert backup_path == memor_dir / "proxy-backup-claude.json"
    assert backup_path.exists()
    
    backup_data = json.loads(backup_path.read_text())
    assert backup_data == {"some": "config"}


def test_backup_agent_config_missing_file(mock_home):
    """Backup handles missing agent config gracefully."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    
    backup_path = backup_agent_config("claude")
    assert backup_path == memor_dir / "proxy-backup-claude.json"
    # Should still create a backup file (empty object)
    assert backup_path.exists()
    backup_data = json.loads(backup_path.read_text())
    assert backup_data == {}


def test_install_claude_proxy(mock_home):
    """Claude proxy install merges ANTHROPIC_BASE_URL into settings.json."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    
    claude_dir = mock_home / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    
    # Test with existing settings
    settings_path.write_text(json.dumps({"existing": "value"}, indent=2))
    
    install_claude_proxy(8421)
    
    # Verify settings were updated
    settings = json.loads(settings_path.read_text())
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8421"
    assert settings["existing"] == "value"  # preserved
    
    # Verify backup was created
    backup = mock_home / ".memor" / "proxy-backup-claude.json"
    assert backup.exists()
    backup_data = json.loads(backup.read_text())
    assert backup_data["existing"] == "value"
    assert "env" not in backup_data  # original didn't have env


def test_install_claude_proxy_no_existing_config(mock_home):
    """Claude proxy install creates settings.json if it doesn't exist."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    
    claude_dir = mock_home / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    
    install_claude_proxy(8421)
    
    assert settings_path.exists()
    settings = json.loads(settings_path.read_text())
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8421"


def test_install_claude_proxy_preserves_existing_env(mock_home):
    """Claude proxy install preserves existing env variables."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    
    claude_dir = mock_home / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    
    original = {
        "env": {
            "SOME_VAR": "value",
        }
    }
    settings_path.write_text(json.dumps(original, indent=2))
    
    install_claude_proxy(8421)
    
    settings = json.loads(settings_path.read_text())
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8421"
    assert settings["env"]["SOME_VAR"] == "value"


def test_install_codex_proxy(mock_home):
    """Codex proxy install sets openai_base_url in config.toml."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    
    codex_dir = mock_home / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    
    # Start with a basic config
    config_path.write_text('model_provider = "anthropic"\n')
    
    install_codex_proxy(8421)
    
    # Verify config was updated
    config = config_path.read_text()
    assert 'openai_base_url = "http://127.0.0.1:8421/v1"' in config
    assert 'model_provider = "anthropic"' in config  # preserved
    
    # Verify backup
    backup = mock_home / ".memor" / "proxy-backup-codex.toml"
    assert backup.exists()
    backup_text = backup.read_text()
    assert 'model_provider = "anthropic"' in backup_text
    assert "openai_base_url" not in backup_text


def test_install_codex_proxy_no_existing_config(mock_home):
    """Codex proxy install creates config.toml if it doesn't exist."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    
    codex_dir = mock_home / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    
    install_codex_proxy(8421)
    
    assert config_path.exists()
    config = config_path.read_text()
    assert 'openai_base_url = "http://127.0.0.1:8421/v1"' in config


def test_uninstall_agent_proxy_restores_backup(mock_home):
    """Uninstall restores the backed-up config and clears proxy_agent flag."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    
    claude_dir = mock_home / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    
    # Set up original config
    original = {"existing": "value"}
    settings_path.write_text(json.dumps(original, indent=2))
    
    # Install proxy
    install_claude_proxy(8421)
    set_proxy_agent("claude", True)
    
    # Verify proxy is installed
    assert is_proxy_agent("claude")
    settings = json.loads(settings_path.read_text())
    assert "ANTHROPIC_BASE_URL" in settings["env"]
    
    # Uninstall
    uninstall_agent_proxy("claude")
    
    # Verify config was restored
    settings = json.loads(settings_path.read_text())
    assert settings == original
    assert "env" not in settings
    
    # Verify proxy_agent flag was cleared
    assert not is_proxy_agent("claude")


def test_uninstall_codex_proxy_restores_backup(mock_home):
    """Uninstall restores Codex config from backup."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    
    codex_dir = mock_home / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    
    # Set up original config
    original = 'model_provider = "anthropic"\n'
    config_path.write_text(original)
    
    # Install proxy
    install_codex_proxy(8421)
    set_proxy_agent("codex", True)
    
    # Verify proxy is installed
    assert is_proxy_agent("codex")
    config = config_path.read_text()
    assert "openai_base_url" in config
    
    # Uninstall
    uninstall_agent_proxy("codex")
    
    # Verify config was restored
    config = config_path.read_text()
    assert config == original
    assert "openai_base_url" not in config
    
    # Verify proxy_agent flag was cleared
    assert not is_proxy_agent("codex")


def test_roundtrip_install_uninstall_claude(mock_home):
    """Full roundtrip: install, verify, uninstall, verify original restored."""
    # Setup
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    claude_dir = mock_home / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    
    original = {"some": "config", "env": {"OTHER": "var"}}
    settings_path.write_text(json.dumps(original, indent=2))
    
    # Install
    install_claude_proxy(9999)
    set_proxy_agent("claude", True)
    
    settings = json.loads(settings_path.read_text())
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9999"
    assert settings["env"]["OTHER"] == "var"
    assert is_proxy_agent("claude")
    
    # Uninstall
    uninstall_agent_proxy("claude")
    
    settings = json.loads(settings_path.read_text())
    assert settings == original
    assert not is_proxy_agent("claude")


def test_roundtrip_install_uninstall_codex(mock_home):
    """Full roundtrip for Codex."""
    # Setup
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    codex_dir = mock_home / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    
    original = 'model = "gpt-4"\napi_key = "sk-test"\n'
    config_path.write_text(original)
    
    # Install
    install_codex_proxy(9999)
    set_proxy_agent("codex", True)
    
    config = config_path.read_text()
    assert 'openai_base_url = "http://127.0.0.1:9999/v1"' in config
    assert 'model = "gpt-4"' in config
    assert is_proxy_agent("codex")
    
    # Uninstall
    uninstall_agent_proxy("codex")
    
    config = config_path.read_text()
    assert config == original
    assert "openai_base_url" not in config
    assert not is_proxy_agent("codex")


def test_reinstall_does_not_clobber_the_original_backup(mock_home):
    """A second install must not snapshot the already-proxied config."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    claude_dir = mock_home / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"

    original = {"existing": "value"}
    settings_path.write_text(json.dumps(original, indent=2))

    with patch("shutil.which", return_value="/fake/memor-retrieve-mcp"):
        install_claude_proxy(8421)
        install_claude_proxy(9999)

    backup = json.loads((memor_dir / "proxy-backup-claude.json").read_text())
    assert backup == original

    uninstall_agent_proxy("claude")
    assert json.loads(settings_path.read_text()) == original


def test_uninstall_clears_backup_so_next_install_recaptures(mock_home):
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    claude_dir = mock_home / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    backup_path = memor_dir / "proxy-backup-claude.json"

    settings_path.write_text(json.dumps({"round": 1}, indent=2))
    with patch("shutil.which", return_value="/fake/memor-retrieve-mcp"):
        install_claude_proxy(8421)
    uninstall_agent_proxy("claude")
    assert not backup_path.exists()

    # A new pre-proxy config must be captured on the next install.
    settings_path.write_text(json.dumps({"round": 2}, indent=2))
    with patch("shutil.which", return_value="/fake/memor-retrieve-mcp"):
        install_claude_proxy(8421)
    assert json.loads(backup_path.read_text()) == {"round": 2}


def test_codex_base_url_stays_above_the_first_table(mock_home):
    """Appending at EOF would nest the key inside the last table."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    codex_dir = mock_home / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    config_path.write_text(
        'model = "gpt-5"\n'
        "\n"
        "[mcp_servers.something]\n"
        'command = "/usr/bin/something"\n'
    )

    with patch("shutil.which", return_value="/fake/memor-retrieve-mcp"):
        install_codex_proxy(8421)

    import tomllib

    parsed = tomllib.loads(config_path.read_text())
    assert parsed["openai_base_url"] == "http://127.0.0.1:8421/v1"
    assert parsed["model"] == "gpt-5"
    assert "openai_base_url" not in parsed["mcp_servers"]["something"]
    assert parsed["mcp_servers"]["memor_retrieve"]["command"] == "/fake/memor-retrieve-mcp"


def test_codex_base_url_is_updated_in_place_on_reinstall(mock_home):
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    codex_dir = mock_home / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    config_path.write_text('model = "gpt-5"\n')

    with patch("shutil.which", return_value="/fake/memor-retrieve-mcp"):
        install_codex_proxy(8421)
        install_codex_proxy(9999)

    import tomllib

    text = config_path.read_text()
    assert text.count("openai_base_url") == 1
    assert tomllib.loads(text)["openai_base_url"] == "http://127.0.0.1:9999/v1"


def test_install_claude_proxy_registers_mcp(mock_home, monkeypatch):
    """Claude proxy install also registers memor_retrieve MCP server."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    
    claude_dir = mock_home / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    
    # Mock shutil.which to return a fake binary path
    fake_binary = "/fake/path/memor-retrieve-mcp"
    with patch("shutil.which", return_value=fake_binary):
        install_claude_proxy(8421)
    
    # Verify settings include both proxy and MCP
    settings = json.loads(settings_path.read_text())
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8421"
    assert "mcpServers" in settings
    assert "memor_retrieve" in settings["mcpServers"]
    assert settings["mcpServers"]["memor_retrieve"]["command"] == fake_binary
    assert settings["mcpServers"]["memor_retrieve"]["args"] == []
    
    # Verify MCP backup was created
    mcp_backup = memor_dir / "mcp-backup-claude.json"
    assert mcp_backup.exists()


def test_install_codex_proxy_registers_mcp(mock_home, monkeypatch):
    """Codex proxy install also registers memor_retrieve MCP server."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    
    codex_dir = mock_home / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    
    # Mock shutil.which to return a fake binary path
    fake_binary = "/fake/path/memor-retrieve-mcp"
    with patch("shutil.which", return_value=fake_binary):
        install_codex_proxy(8421)
    
    # Verify config includes both proxy and MCP
    config = config_path.read_text()
    assert 'openai_base_url = "http://127.0.0.1:8421/v1"' in config
    assert "[mcp_servers.memor_retrieve]" in config
    assert f'command = "{fake_binary}"' in config
    
    # Verify MCP backup was created
    mcp_backup = memor_dir / "mcp-backup-codex.toml"
    assert mcp_backup.exists()


def test_agent_proxy_handlers_registry():
    """Registry includes all proxy agents with install/uninstall/strip callables."""
    assert set(AGENT_PROXY_HANDLERS) == {
        "claude", "codex", "goose", "kimi", "cursor", "cline", "opencode",
    }
    for agent, handler in AGENT_PROXY_HANDLERS.items():
        assert callable(handler.install)
        assert callable(handler.uninstall)
        assert callable(handler.strip)
        assert callable(handler.paths)


def test_install_agent_proxy_dispatches_claude(mock_home):
    """install_agent_proxy dispatches to the claude handler."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    (mock_home / ".claude").mkdir()

    with patch("shutil.which", return_value="/fake/memor-retrieve-mcp"):
        install_agent_proxy("claude", 8421)

    settings = json.loads((mock_home / ".claude" / "settings.json").read_text())
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8421"


def test_install_agent_proxy_unknown_agent_raises():
    with pytest.raises(ValueError, match="Unknown agent"):
        install_agent_proxy("unknown", 8421)


def test_install_claude_proxy_captures_default_upstream(mock_home):
    """Fresh Claude install records the default Anthropic upstream."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    (mock_home / ".claude").mkdir()

    with patch("shutil.which", return_value="/fake/memor-retrieve-mcp"):
        install_claude_proxy(8421)

    upstream = get_proxy_upstream("claude")
    assert upstream is not None
    assert upstream["protocol"] == "anthropic"
    assert upstream["base_url"] == "https://api.anthropic.com/v1/messages"
    assert upstream["provider_name"] == "anthropic"


def test_install_claude_proxy_captures_custom_upstream(mock_home):
    """Claude install captures a pre-existing non-localhost ANTHROPIC_BASE_URL."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    claude_dir = mock_home / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_BASE_URL": "https://custom.example.com",
                }
            },
            indent=2,
        )
    )

    with patch("shutil.which", return_value="/fake/memor-retrieve-mcp"):
        install_claude_proxy(8421)

    upstream = get_proxy_upstream("claude")
    assert upstream["base_url"] == "https://custom.example.com/v1/messages"


def test_install_claude_proxy_ignores_memor_localhost_upstream(mock_home):
    """Re-install when already proxied should not capture localhost as upstream."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    claude_dir = mock_home / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    settings_path.write_text(
        json.dumps(
            {"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8421"}},
            indent=2,
        )
    )

    with patch("shutil.which", return_value="/fake/memor-retrieve-mcp"):
        install_claude_proxy(8421)

    upstream = get_proxy_upstream("claude")
    assert upstream["base_url"] == "https://api.anthropic.com/v1/messages"


def test_install_codex_proxy_captures_default_upstream(mock_home):
    """Fresh Codex install records the default OpenAI upstream."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    (mock_home / ".codex").mkdir()

    with patch("shutil.which", return_value="/fake/memor-retrieve-mcp"):
        install_codex_proxy(8421)

    upstream = get_proxy_upstream("codex")
    assert upstream is not None
    assert upstream["protocol"] == "openai"
    assert upstream["base_url"] == "https://api.openai.com/v1/chat/completions"
    assert upstream["provider_name"] == "openai"


def test_install_codex_proxy_captures_custom_upstream(mock_home):
    """Codex install captures a pre-existing non-localhost openai_base_url."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    codex_dir = mock_home / ".codex"
    codex_dir.mkdir()
    config_path = codex_dir / "config.toml"
    config_path.write_text('openai_base_url = "https://custom.example.com/v1"\n')

    with patch("shutil.which", return_value="/fake/memor-retrieve-mcp"):
        install_codex_proxy(8421)

    upstream = get_proxy_upstream("codex")
    assert upstream["base_url"] == "https://custom.example.com/v1/chat/completions"


def test_uninstall_clears_proxy_upstream(mock_home):
    """Uninstall removes the captured upstream entry."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    (mock_home / ".claude").mkdir()

    with patch("shutil.which", return_value="/fake/memor-retrieve-mcp"):
        install_claude_proxy(8421)
    assert get_proxy_upstream("claude") is not None

    uninstall_agent_proxy("claude")
    assert get_proxy_upstream("claude") is None


def test_uninstall_removes_mcp_server(mock_home):
    """Uninstall removes both proxy URL and MCP server by restoring original config."""
    memor_dir = mock_home / ".memor"
    memor_dir.mkdir()
    
    claude_dir = mock_home / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    
    # Start with clean settings
    original = {"existing": "value"}
    settings_path.write_text(json.dumps(original, indent=2))
    
    # Install proxy (which includes MCP)
    fake_binary = "/fake/path/memor-retrieve-mcp"
    with patch("shutil.which", return_value=fake_binary):
        install_claude_proxy(8421)
    
    set_proxy_agent("claude", True)
    
    # Verify both proxy and MCP are present
    settings = json.loads(settings_path.read_text())
    assert "ANTHROPIC_BASE_URL" in settings["env"]
    assert "mcpServers" in settings
    assert "memor_retrieve" in settings["mcpServers"]
    
    # Uninstall
    uninstall_agent_proxy("claude")
    
    # Verify both proxy and MCP are removed (restored to original)
    settings = json.loads(settings_path.read_text())
    assert settings == original
    assert "env" not in settings
    assert "mcpServers" not in settings
