"""Tests for Kimi proxy install/uninstall."""
from __future__ import annotations

import pytest
import tomllib

import memor.config
from memor.config import get_proxy_upstream, is_proxy_agent
from memor.proxy.kimi_install import (
    KimiConfigError,
    discover_kimi_upstream,
    install_kimi_proxy,
    strip_kimi_proxy_urls,
    uninstall_kimi_proxy,
)
from memor.proxy.install import backup_agent_config, failover_proxy_agents


@pytest.fixture
def mock_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(memor.config, "STATE_DIR", tmp_path / ".memor")
    monkeypatch.setattr(memor.config, "CONFIG_PATH", tmp_path / ".memor" / "config.json")
    return tmp_path


def _kimi_config_path(home):
    return home / ".kimi" / "config.toml"


def _setup_kimi_config(
    home,
    *,
    provider_key: str = "managed:kimi-code",
    provider_type: str = "kimi",
    base_url: str | None = "https://api.kimi.com/coding/v1",
    custom_headers: str | None = None,
    model_key: str = "kimi-code/k3",
):
    kimi_dir = home / ".kimi"
    kimi_dir.mkdir(parents=True)
    config_path = kimi_dir / "config.toml"
    lines = [
        f'default_model = "{model_key}"',
        "",
        f'[providers."{provider_key}"]',
        f'type = "{provider_type}"',
    ]
    if base_url is not None:
        lines.append(f'base_url = "{base_url}"')
    if custom_headers is not None:
        lines.append(custom_headers)
    lines.extend(
        [
            "",
            f'[models."{model_key}"]',
            f'provider = "{provider_key}"',
            'model = "k3"',
            "max_context_size = 1048576",
            "",
        ]
    )
    config_path.write_text("\n".join(lines))
    return config_path


def test_discover_kimi_upstream_openai_compat(mock_home):
    config_path = _setup_kimi_config(mock_home)
    protocol, base_url, provider_key = discover_kimi_upstream(config_path)
    assert protocol == "openai"
    assert base_url == "https://api.kimi.com/coding/v1/chat/completions"
    assert provider_key == "managed:kimi-code"


def test_discover_kimi_upstream_anthropic(mock_home):
    config_path = _setup_kimi_config(
        mock_home,
        provider_type="anthropic",
        base_url="https://api.kimi.com/coding/v1",
        model_key="anthropic-model",
        provider_key="anthropic",
    )
    protocol, base_url, _ = discover_kimi_upstream(config_path)
    assert protocol == "anthropic"
    assert base_url == "https://api.kimi.com/coding/v1/messages"


def test_discover_kimi_upstream_from_env_subtable(mock_home):
    config_path = _setup_kimi_config(mock_home, base_url=None)
    text = config_path.read_text()
    config_path.write_text(
        text
        + '\n[providers."managed:kimi-code".env]\n'
        + 'KIMI_BASE_URL = "https://api.moonshot.ai/v1"\n'
    )
    protocol, base_url, _ = discover_kimi_upstream(config_path)
    assert protocol == "openai"
    assert base_url == "https://api.moonshot.ai/v1/chat/completions"


def test_discover_kimi_upstream_missing_base_url(mock_home):
    config_path = _setup_kimi_config(mock_home, base_url=None)
    with pytest.raises(KimiConfigError, match="writable base_url"):
        discover_kimi_upstream(config_path)


def test_install_kimi_proxy_rewrites_config_and_sets_upstream(mock_home):
    config_path = _setup_kimi_config(
        mock_home,
        custom_headers='custom_headers = { "Authorization" = "Bearer secret" }',
    )
    install_kimi_proxy(8421)

    parsed = tomllib.loads(config_path.read_text())
    provider = parsed["providers"]["managed:kimi-code"]
    assert provider["base_url"] == "http://127.0.0.1:8421/v1"
    assert provider["custom_headers"]["x-agent"] == "kimi"
    assert provider["custom_headers"]["Authorization"] == "Bearer secret"

    upstream = get_proxy_upstream("kimi")
    assert upstream is not None
    assert upstream["protocol"] == "openai"
    assert upstream["base_url"] == "https://api.kimi.com/coding/v1/chat/completions"
    assert upstream["provider_name"] == "managed:kimi-code"
    assert is_proxy_agent("kimi")


def test_install_kimi_proxy_anthropic_rewrite(mock_home):
    config_path = _setup_kimi_config(
        mock_home,
        provider_type="anthropic",
        provider_key="anthropic",
        model_key="anthropic-model",
        base_url="https://api.kimi.com/coding/v1",
    )
    install_kimi_proxy(8421)
    parsed = tomllib.loads(config_path.read_text())
    assert parsed["providers"]["anthropic"]["base_url"] == "http://127.0.0.1:8421"
    upstream = get_proxy_upstream("kimi")
    assert upstream["protocol"] == "anthropic"


def test_backup_agent_config_kimi(mock_home):
    config_path = _setup_kimi_config(mock_home)
    (mock_home / ".memor").mkdir()
    backup_path = backup_agent_config("kimi")
    assert backup_path.exists()
    assert backup_path.read_text() == config_path.read_text()


def test_uninstall_kimi_proxy_restores_backup(mock_home):
    config_path = _setup_kimi_config(mock_home)
    original = config_path.read_text()
    install_kimi_proxy(8421)
    assert "127.0.0.1:8421" in config_path.read_text()

    uninstall_kimi_proxy()
    assert config_path.read_text() == original
    assert get_proxy_upstream("kimi") is None
    assert not is_proxy_agent("kimi")


def test_strip_kimi_proxy_urls_without_backup(mock_home):
    config_path = _setup_kimi_config(
        mock_home,
        custom_headers='custom_headers = { "x-agent" = "kimi", "Other" = "1" }',
    )
    config_path.write_text(
        config_path.read_text().replace(
            'base_url = "https://api.kimi.com/coding/v1"',
            'base_url = "http://127.0.0.1:8421/v1"',
        )
    )
    msg = strip_kimi_proxy_urls(8421)
    assert "removed" in msg
    parsed = tomllib.loads(config_path.read_text())
    provider = parsed["providers"]["managed:kimi-code"]
    assert "base_url" not in provider
    assert provider["custom_headers"]["Other"] == "1"
    assert "x-agent" not in provider["custom_headers"]


def test_failover_restores_kimi_backup(mock_home):
    config_path = _setup_kimi_config(mock_home)
    original = config_path.read_text()
    install_kimi_proxy(8421)
    assert is_proxy_agent("kimi")

    lines = failover_proxy_agents("health check failed")
    assert any("kimi" in ln and "restored" in ln for ln in lines)
    assert not is_proxy_agent("kimi")
    assert get_proxy_upstream("kimi") is None
    assert config_path.read_text() == original
