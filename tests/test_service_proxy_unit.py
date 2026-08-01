"""Tests that the service module includes the proxy unit."""
from __future__ import annotations

from unittest.mock import MagicMock
import memor.service as svc


def test_proxy_unit_in_units_when_with_proxy():
    """Verify proxy unit is included when with_proxy=True."""
    units = svc._units("/bin/memor", with_dashboard=True, with_proxy=True, port=8420)
    keys = {u["key"] for u in units}
    assert "proxy" in keys
    
    proxy = next(u for u in units if u["key"] == "proxy")
    assert proxy["label"] == svc.PROXY_LABEL
    assert proxy["args"] == ["/bin/memor", "proxy", "--port", "8421"]
    assert str(proxy["log"]) == str(svc.PROXY_LOG)


def test_proxy_unit_not_in_units_when_with_proxy_false():
    """Verify proxy unit is excluded when with_proxy=False."""
    units = svc._units("/bin/memor", with_dashboard=True, with_proxy=False, port=8420)
    keys = {u["key"] for u in units}
    assert "proxy" not in keys


def test_proxy_label_in_all_unit_labels():
    """Verify proxy label is in _all_unit_labels for uninstall/stop/status."""
    labels = svc._all_unit_labels()
    label_list = [label for label, _ in labels]
    assert svc.PROXY_LABEL in label_list


def test_install_with_proxy_writes_proxy_plist(monkeypatch, tmp_path):
    """Test that install(with_proxy=True) writes the proxy unit on macOS."""
    monkeypatch.setattr(svc, "_is_macos", lambda: True)
    monkeypatch.setattr(svc, "_find_memor_bin", lambda: "/bin/memor")
    monkeypatch.setattr(svc, "PLIST_DIR", tmp_path)
    monkeypatch.setattr(svc, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(svc, "_port_in_use", lambda port: False)
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(svc.subprocess, "run", run)
    
    out = svc.install(with_dashboard=True, with_proxy=True)
    
    # Verify proxy plist was created
    proxy_plist = tmp_path / f"{svc.PROXY_LABEL}.plist"
    assert proxy_plist.exists()
    
    proxy_content = proxy_plist.read_text()
    assert "<string>proxy</string>" in proxy_content
    assert "<string>--port</string>" in proxy_content
    assert "<string>8421</string>" in proxy_content
    assert str(svc.PROXY_LOG) in proxy_content
    
    # Verify bootstrap was called for proxy
    bootstraps = [c for c in run.call_args_list if "bootstrap" in c.args[0]]
    assert len(bootstraps) == 3  # daemon, dashboard, proxy
    
    assert "proxy" in out


def test_install_without_proxy_no_proxy_plist(monkeypatch, tmp_path):
    """Test that install(with_proxy=False) doesn't write proxy unit."""
    monkeypatch.setattr(svc, "_is_macos", lambda: True)
    monkeypatch.setattr(svc, "_find_memor_bin", lambda: "/bin/memor")
    monkeypatch.setattr(svc, "PLIST_DIR", tmp_path)
    monkeypatch.setattr(svc, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(svc, "_port_in_use", lambda port: False)
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(svc.subprocess, "run", run)
    
    svc.install(with_dashboard=True, with_proxy=False)
    
    # Verify proxy plist was NOT created
    proxy_plist = tmp_path / f"{svc.PROXY_LABEL}.plist"
    assert not proxy_plist.exists()
    
    # Verify only 2 bootstraps (daemon, dashboard)
    bootstraps = [c for c in run.call_args_list if "bootstrap" in c.args[0]]
    assert len(bootstraps) == 2


def test_uninstall_removes_proxy_plist(monkeypatch, tmp_path):
    """Test that uninstall removes the proxy plist."""
    monkeypatch.setattr(svc, "_is_macos", lambda: True)
    monkeypatch.setattr(svc, "PLIST_DIR", tmp_path)
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(svc.subprocess, "run", run)
    
    # Create proxy plist
    proxy_plist = tmp_path / f"{svc.PROXY_LABEL}.plist"
    proxy_plist.write_text("test")
    
    out = svc.uninstall()
    
    # Verify proxy plist was removed
    assert not proxy_plist.exists()
    assert str(proxy_plist) in out


def test_status_shows_proxy_unit(monkeypatch, tmp_path):
    """Test that status includes proxy unit when installed."""
    monkeypatch.setattr(svc, "_is_macos", lambda: True)
    monkeypatch.setattr(svc, "PLIST_DIR", tmp_path)
    
    # Create proxy plist
    proxy_plist = tmp_path / f"{svc.PROXY_LABEL}.plist"
    proxy_plist.write_text("test")
    
    def mock_status(label):
        if label == svc.PROXY_LABEL:
            return "running (pid 12345)"
        return "not installed"
    
    monkeypatch.setattr(svc, "_macos_unit_status", mock_status)
    
    out = svc.status()
    
    assert "proxy: running (pid 12345)" in out
