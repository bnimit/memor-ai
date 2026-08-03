"""Tests for Cursor proxy install/uninstall."""
from __future__ import annotations

import json

import pytest

import memor.config
from memor.config import get_proxy_upstream, is_proxy_agent
from memor.proxy.cursor_install import (
    install_cursor_proxy,
    proxy_openai_base_url,
    strip_cursor_proxy_urls,
    uninstall_cursor_proxy,
)
from memor.proxy.install import backup_agent_config, failover_proxy_agents
from memor.proxy.vscode_settings import vscode_user_settings_path


@pytest.fixture
def mock_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(memor.config, "STATE_DIR", tmp_path / ".memor")
    monkeypatch.setattr(memor.config, "CONFIG_PATH", tmp_path / ".memor" / "config.json")
    return tmp_path


def test_install_cursor_proxy_openai(mock_home):
    settings_path = vscode_user_settings_path("Cursor")
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"openai.baseUrl": "https://api.openai.com/v1"}) + "\n"
    )

    lines = install_cursor_proxy(8421)
    assert any("BYOK" in line for line in lines)
    assert is_proxy_agent("cursor")

    settings = json.loads(settings_path.read_text())
    assert settings["openai.baseUrl"] == proxy_openai_base_url(8421)

    upstream = get_proxy_upstream("cursor")
    assert upstream["protocol"] == "openai"
    assert upstream["base_url"].endswith("/chat/completions")


def test_install_cursor_proxy_upstream_url_flag(mock_home):
    settings_path = vscode_user_settings_path("Cursor")
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{}\n")

    install_cursor_proxy(8421, upstream_url="https://api.deepseek.com/v1")
    upstream = get_proxy_upstream("cursor")
    assert upstream["base_url"] == "https://api.deepseek.com/v1/chat/completions"


def test_uninstall_cursor_proxy_restores_backup(mock_home):
    settings_path = vscode_user_settings_path("Cursor")
    settings_path.parent.mkdir(parents=True)
    original = {"editor.fontSize": 14}
    settings_path.write_text(json.dumps(original) + "\n")

    backup_agent_config("cursor")
    install_cursor_proxy(8421)
    uninstall_cursor_proxy()

    assert json.loads(settings_path.read_text()) == original
    assert not is_proxy_agent("cursor")


def test_failover_cursor_proxy(mock_home):
    settings_path = vscode_user_settings_path("Cursor")
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"openai.baseUrl": "https://api.openai.com/v1"}) + "\n")

    install_cursor_proxy(8421)
    lines = failover_proxy_agents("health check failed")
    assert any("cursor" in line for line in lines)
    assert not is_proxy_agent("cursor")


def test_strip_cursor_proxy_urls(mock_home):
    settings_path = vscode_user_settings_path("Cursor")
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"openai.baseUrl": proxy_openai_base_url(8421)}) + "\n"
    )

    msg = strip_cursor_proxy_urls(8421)
    assert "removed" in msg
    assert "openai.baseUrl" not in json.loads(settings_path.read_text())
