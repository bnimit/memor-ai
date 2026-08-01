"""Tests for Goose proxy install/uninstall."""
from __future__ import annotations

import json
import pytest

import memor.config
from memor.config import get_proxy_upstream, is_proxy_agent
from memor.proxy.goose_install import (
    GooseProviderNotFoundError,
    discover_goose_upstream,
    install_goose_proxy,
    strip_goose_proxy_urls,
    uninstall_goose_proxy,
)
from memor.proxy.install import backup_agent_config, failover_proxy_agents


@pytest.fixture
def mock_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(memor.config, "STATE_DIR", tmp_path / ".memor")
    monkeypatch.setattr(memor.config, "CONFIG_PATH", tmp_path / ".memor" / "config.json")
    return tmp_path


def _setup_goose_custom_provider(
    home,
    *,
    provider_name: str = "custom_foo",
    base_url: str = "https://api.example.com/v1/chat/completions",
    engine: str = "openai",
    headers: dict | None = None,
):
    goose_dir = home / ".config" / "goose"
    goose_dir.mkdir(parents=True)
    (goose_dir / "config.yaml").write_text(f"active_provider: {provider_name}\n")
    providers_dir = goose_dir / "custom_providers"
    providers_dir.mkdir()
    payload = {"engine": engine, "base_url": base_url}
    if headers is not None:
        payload["headers"] = headers
    provider_path = providers_dir / f"{provider_name}.json"
    provider_path.write_text(json.dumps(payload, indent=2) + "\n")
    return goose_dir / "config.yaml", provider_path


def test_discover_goose_upstream_custom_openai(mock_home):
    _setup_goose_custom_provider(
        mock_home,
        provider_name="custom_deepseek",
        base_url="https://api.deepseek.com/v1/chat/completions",
    )
    protocol, base_url, provider_name, rewrite_kind = discover_goose_upstream()
    assert protocol == "openai"
    assert base_url == "https://api.deepseek.com/v1/chat/completions"
    assert provider_name == "custom_deepseek"
    assert rewrite_kind == "custom_json"


def test_discover_goose_upstream_custom_anthropic(mock_home):
    _setup_goose_custom_provider(
        mock_home,
        provider_name="custom_claude",
        base_url="https://api.anthropic.com/v1/messages",
        engine="anthropic",
    )
    protocol, base_url, provider_name, rewrite_kind = discover_goose_upstream()
    assert protocol == "anthropic"
    assert base_url == "https://api.anthropic.com/v1/messages"
    assert rewrite_kind == "custom_json"


def test_discover_goose_upstream_missing_custom_provider(mock_home):
    goose_dir = mock_home / ".config" / "goose"
    goose_dir.mkdir(parents=True)
    (goose_dir / "config.yaml").write_text("active_provider: custom_missing\n")
    with pytest.raises(GooseProviderNotFoundError, match="--upstream-url"):
        discover_goose_upstream()


def test_discover_goose_upstream_materializes_desktop_provider(mock_home):
    """Goose Desktop can register custom providers in yaml without JSON."""
    goose_dir = mock_home / ".config" / "goose"
    goose_dir.mkdir(parents=True)
    (goose_dir / "config.yaml").write_text(
        "active_provider: custom_deepseek\n"
        "providers:\n"
        "  custom_deepseek:\n"
        "    enabled: true\n"
        "    model: deepseek-v4-pro\n"
        "    configured: true\n"
    )
    protocol, base_url, provider_name, rewrite_kind = discover_goose_upstream(
        upstream_url="https://api.deepseek.com/v1/chat/completions",
    )
    assert protocol == "openai"
    assert base_url == "https://api.deepseek.com/v1/chat/completions"
    assert provider_name == "custom_deepseek"
    assert rewrite_kind == "custom_json"
    provider_path = goose_dir / "custom_providers" / "custom_deepseek.json"
    assert provider_path.exists()
    data = json.loads(provider_path.read_text())
    assert data["base_url"] == "https://api.deepseek.com/v1/chat/completions"
    assert data["models"][0]["name"] == "deepseek-v4-pro"


def test_install_goose_proxy_materializes_from_upstream_url(mock_home, monkeypatch):
    goose_dir = mock_home / ".config" / "goose"
    goose_dir.mkdir(parents=True)
    (goose_dir / "config.yaml").write_text(
        "active_provider: custom_deepseek\n"
        "providers:\n"
        "  custom_deepseek:\n"
        "    model: deepseek-v4-pro\n"
    )
    install_goose_proxy(
        8421,
        upstream_url="https://api.deepseek.com/v1/chat/completions",
    )
    provider_path = goose_dir / "custom_providers" / "custom_deepseek.json"
    data = json.loads(provider_path.read_text())
    assert data["base_url"] == "http://127.0.0.1:8421/v1/chat/completions"
    assert data["headers"]["x-agent"] == "goose"
    assert get_proxy_upstream("goose")["base_url"].endswith("/chat/completions")


def test_install_goose_proxy_custom_rewrites_json_and_sets_upstream(mock_home):
    _, provider_path = _setup_goose_custom_provider(
        mock_home,
        provider_name="custom_foo",
        base_url="https://api.example.com/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
    )
    install_goose_proxy(8421)

    data = json.loads(provider_path.read_text())
    assert data["base_url"] == "http://127.0.0.1:8421/v1/chat/completions"
    assert data["headers"]["x-agent"] == "goose"
    assert data["headers"]["Authorization"] == "Bearer secret"

    upstream = get_proxy_upstream("goose")
    assert upstream is not None
    assert upstream["protocol"] == "openai"
    assert upstream["base_url"] == "https://api.example.com/v1/chat/completions"
    assert upstream["provider_name"] == "custom_foo"
    assert is_proxy_agent("goose")


def test_install_goose_proxy_anthropic_custom(mock_home):
    _, provider_path = _setup_goose_custom_provider(
        mock_home,
        provider_name="custom_claude",
        base_url="https://proxy.example.com/v1/messages",
        engine="anthropic",
    )
    install_goose_proxy(8421)
    data = json.loads(provider_path.read_text())
    assert data["base_url"] == "http://127.0.0.1:8421/v1/messages"
    upstream = get_proxy_upstream("goose")
    assert upstream["protocol"] == "anthropic"


def test_backup_agent_config_goose(mock_home):
    config_path, _ = _setup_goose_custom_provider(mock_home)
    backup_path = backup_agent_config("goose")
    assert backup_path.exists()
    assert backup_path.read_text() == config_path.read_text()


def test_uninstall_goose_proxy_restores_backups(mock_home):
    config_path, provider_path = _setup_goose_custom_provider(
        mock_home,
        base_url="https://api.example.com/v1/chat/completions",
    )
    original_provider = provider_path.read_text()
    install_goose_proxy(8421)
    assert json.loads(provider_path.read_text())["base_url"].startswith("http://127.0.0.1:")

    uninstall_goose_proxy()
    assert config_path.read_text() == "active_provider: custom_foo\n"
    assert provider_path.read_text() == original_provider
    assert get_proxy_upstream("goose") is None
    assert not is_proxy_agent("goose")


def test_strip_goose_proxy_urls_custom_without_backup(mock_home):
    _, provider_path = _setup_goose_custom_provider(mock_home)
    provider_path.write_text(
        json.dumps(
            {
                "engine": "openai",
                "base_url": "http://127.0.0.1:8421/v1/chat/completions",
                "headers": {"x-agent": "goose", "Other": "1"},
            },
            indent=2,
        )
        + "\n"
    )
    msg = strip_goose_proxy_urls(8421)
    assert "stripped" in msg
    data = json.loads(provider_path.read_text())
    assert "base_url" not in data
    assert data.get("headers", {}).get("x-agent") != "goose"
    assert data["headers"]["Other"] == "1"


def test_failover_restores_goose_backup(mock_home):
    config_path, provider_path = _setup_goose_custom_provider(mock_home)
    original_provider = provider_path.read_text()
    install_goose_proxy(8421)
    assert is_proxy_agent("goose")

    lines = failover_proxy_agents("health check failed")
    assert any("goose" in ln and "restored" in ln for ln in lines)
    assert not is_proxy_agent("goose")
    assert get_proxy_upstream("goose") is None
    assert provider_path.read_text() == original_provider
    assert config_path.read_text() == "active_provider: custom_foo\n"
