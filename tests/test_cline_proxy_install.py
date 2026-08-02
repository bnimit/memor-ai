"""Tests for Cline proxy install/uninstall."""
from __future__ import annotations

import json

import pytest

import memor.config
from memor.config import get_proxy_upstream, is_proxy_agent
from memor.proxy.cline_install import (
    install_cline_proxy,
    proxy_openai_base_url,
    strip_cline_proxy_urls,
    uninstall_cline_proxy,
)
from memor.proxy.install import backup_agent_config
from memor.proxy.vscode_settings import vscode_user_settings_path


@pytest.fixture
def mock_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(memor.config, "STATE_DIR", tmp_path / ".memor")
    monkeypatch.setattr(memor.config, "CONFIG_PATH", tmp_path / ".memor" / "config.json")
    return tmp_path


def test_install_cline_proxy_openai_compatible(mock_home):
    settings_path = vscode_user_settings_path("Code")
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "cline.apiProvider": "openai-compatible",
                "cline.openAiCompatibleBaseUrl": "https://api.example.com/v1",
            }
        )
        + "\n"
    )

    notes = install_cline_proxy(8421)
    assert any("Cline settings" in line for line in notes)
    assert is_proxy_agent("cline")

    settings = json.loads(settings_path.read_text())
    assert settings["cline.openAiBaseUrl"] == proxy_openai_base_url(8421)

    upstream = get_proxy_upstream("cline")
    assert upstream["protocol"] == "openai"
    assert "example.com" in upstream["base_url"]


def test_uninstall_cline_proxy(mock_home):
    settings_path = vscode_user_settings_path("Code")
    settings_path.parent.mkdir(parents=True)
    original = {"cline.apiProvider": "openai-compatible"}
    settings_path.write_text(json.dumps(original) + "\n")

    backup_agent_config("cline")
    install_cline_proxy(8421, upstream_url="https://api.openai.com/v1")
    uninstall_cline_proxy()

    assert json.loads(settings_path.read_text()) == original
    assert not is_proxy_agent("cline")


def test_strip_cline_proxy_urls(mock_home):
    settings_path = vscode_user_settings_path("Code")
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"cline.openAiBaseUrl": proxy_openai_base_url(8421)}) + "\n"
    )

    msg = strip_cline_proxy_urls(8421)
    assert "removed" in msg
