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

def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    assert cfg.proxy_port() == 8421
    assert cfg.ccr_ttl_seconds() == 7 * 86400
    assert cfg.ccr_max_bytes() == 2 * 1024**3
