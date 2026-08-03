"""Tests for OpenCode proxy install/uninstall."""
from __future__ import annotations

import json

import pytest

import memor.config
from memor.config import get_proxy_upstream, is_proxy_agent
from memor.proxy.opencode_install import (
    OpenCodeConfigError,
    discover_opencode_upstream,
    install_opencode_proxy,
    opencode_config_path,
    strip_opencode_proxy_urls,
    uninstall_opencode_proxy,
)
from memor.proxy.install import backup_agent_config


@pytest.fixture
def mock_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(memor.config, "STATE_DIR", tmp_path / ".memor")
    monkeypatch.setattr(memor.config, "CONFIG_PATH", tmp_path / ".memor" / "config.json")
    return tmp_path


def _write_opencode(home, payload: dict) -> None:
    path = home / ".config" / "opencode" / "opencode.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def test_discover_opencode_upstream(mock_home):
    _write_opencode(
        mock_home,
        {
            "model": "openai/gpt-4o",
            "provider": {
                "openai": {
                    "options": {"baseURL": "https://api.openai.com/v1"},
                }
            },
        },
    )
    config_path = opencode_config_path()
    protocol, base_url, provider_id, _ = discover_opencode_upstream(config_path)
    assert protocol == "openai"
    assert base_url.endswith("/chat/completions")
    assert provider_id == "openai"


def test_install_opencode_proxy(mock_home):
    _write_opencode(
        mock_home,
        {
            "model": "openai/gpt-4o",
            "provider": {
                "openai": {
                    "options": {"baseURL": "https://api.openai.com/v1"},
                }
            },
        },
    )

    install_opencode_proxy(8421)
    assert is_proxy_agent("opencode")

    config = json.loads(opencode_config_path().read_text())
    options = config["provider"]["openai"]["options"]
    assert options["baseURL"] == "http://127.0.0.1:8421/opencode/v1"
    assert options["headers"]["x-agent"] == "opencode"

    upstream = get_proxy_upstream("opencode")
    assert upstream["base_url"].endswith("/chat/completions")


def test_discover_opencode_missing_base_url(mock_home):
    _write_opencode(
        mock_home,
        {
            "model": "openai/gpt-4o",
            "provider": {"openai": {"options": {}}},
        },
    )
    with pytest.raises(OpenCodeConfigError, match="baseURL"):
        discover_opencode_upstream(opencode_config_path())


def test_uninstall_opencode_proxy(mock_home):
    _write_opencode(
        mock_home,
        {
            "model": "openai/gpt-4o",
            "provider": {
                "openai": {
                    "options": {"baseURL": "https://api.openai.com/v1"},
                }
            },
        },
    )
    backup_agent_config("opencode")
    install_opencode_proxy(8421)
    uninstall_opencode_proxy()

    config = json.loads(opencode_config_path().read_text())
    assert config["provider"]["openai"]["options"]["baseURL"] == "https://api.openai.com/v1"
    assert not is_proxy_agent("opencode")


def test_strip_opencode_proxy_urls(mock_home):
    _write_opencode(
        mock_home,
        {
            "model": "openai/gpt-4o",
            "provider": {
                "openai": {
                    "options": {
                        "baseURL": "http://127.0.0.1:8421/opencode/v1",
                        "headers": {"x-agent": "opencode"},
                    },
                }
            },
        },
    )
    msg = strip_opencode_proxy_urls(8421)
    assert "removed" in msg
