"""Shared pytest fixtures."""
import pytest


@pytest.fixture(autouse=True)
def isolated_memor_config(tmp_path, monkeypatch):
    """Keep tests off the developer's ~/.memor config (proxy flags, upstreams)."""
    import memor.config as cfg

    state_dir = tmp_path / "memor-state"
    state_dir.mkdir()
    monkeypatch.setattr(cfg, "STATE_DIR", state_dir)
    monkeypatch.setattr(cfg, "CONFIG_PATH", state_dir / "config.json")
