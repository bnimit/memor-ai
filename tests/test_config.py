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


def test_enabling_compression_stamps_a_fresh_boundary():
    """The timestamp marks when THIS run began, not some earlier one."""
    import time

    from memor.config import load_config, set_compress_older_turns

    set_compress_older_turns(True)
    first = load_config()["compress_started_at"]
    assert abs(first - time.time()) < 5

    set_compress_older_turns(False)
    assert "compress_started_at" not in load_config()

    set_compress_older_turns(True)
    second = load_config()["compress_started_at"]
    assert second >= first, "re-enabling must re-stamp, not reuse a stale boundary"


def test_disabling_clears_the_boundary():
    from memor.config import load_config, set_compress_older_turns

    set_compress_older_turns(True)
    set_compress_older_turns(False)
    cfg = load_config()
    assert cfg["compress_older_turns"] is False
    assert cfg.get("compress_started_at") is None
