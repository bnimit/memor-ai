from pathlib import Path
import memor.config as cfg

def test_proxy_agent_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
    assert cfg.is_proxy_agent("claude") is False
    cfg.set_proxy_agent("claude", True)
    assert cfg.is_proxy_agent("claude") is True
    assert cfg.is_proxy_agent("cursor") is False
    cfg.set_proxy_agent("claude", False)
    assert cfg.is_proxy_agent("claude") is False

def test_proxy_upstream_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
    assert cfg.get_proxy_upstream("goose") is None
    cfg.set_proxy_upstream("goose", protocol="openai",
        base_url="https://api.deepseek.com/v1/chat/completions",
        provider_name="custom_deepseek")
    u = cfg.get_proxy_upstream("goose")
    assert u["protocol"] == "openai"
    assert u["base_url"].endswith("/chat/completions")
    assert u["provider_name"] == "custom_deepseek"
    cfg.clear_proxy_upstream("goose")
    assert cfg.get_proxy_upstream("goose") is None

def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    assert cfg.proxy_port() == 8421
    assert cfg.ccr_ttl_seconds() == 7 * 86400
    assert cfg.ccr_max_bytes() == 2 * 1024**3
