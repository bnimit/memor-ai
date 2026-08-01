"""Tests that the service module includes the proxy unit."""
from __future__ import annotations

from unittest.mock import MagicMock
import memor.service as svc


def test_proxy_unit_absent_by_default():
    """The proxy is opt-in: plain `memor service install` must not install it."""
    units = svc._units("/bin/memor", port=8420)
    assert [u["key"] for u in units] == ["daemon", "dashboard"]


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


def test_install_with_proxy_warns_when_proxy_port_in_use(monkeypatch, tmp_path):
    """install(with_proxy=True) should warn if the proxy port is already taken."""
    monkeypatch.setattr(svc, "_is_macos", lambda: True)
    monkeypatch.setattr(svc, "_find_memor_bin", lambda: "/bin/memor")
    monkeypatch.setattr(svc, "PLIST_DIR", tmp_path)
    monkeypatch.setattr(svc, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(svc, "_proxy_port", lambda: 8421)
    monkeypatch.setattr(svc, "_port_in_use", lambda port: port == 8421)
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(svc.subprocess, "run", run)

    out = svc.install(with_dashboard=False, with_proxy=True)
    assert "port 8421 is already in use" in out
    assert "proxy service may" in out


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
    """Test that install(with_proxy=False) doesn't write proxy unit when not opted in."""
    monkeypatch.setattr(svc, "_is_macos", lambda: True)
    monkeypatch.setattr(svc, "_find_memor_bin", lambda: "/bin/memor")
    monkeypatch.setattr(svc, "PLIST_DIR", tmp_path)
    monkeypatch.setattr(svc, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(svc, "_port_in_use", lambda port: False)
    monkeypatch.setattr(svc, "_should_run_proxy", lambda: False)
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(svc.subprocess, "run", run)
    
    svc.install(with_dashboard=True, with_proxy=False)
    
    # Verify proxy plist was NOT created
    proxy_plist = tmp_path / f"{svc.PROXY_LABEL}.plist"
    assert not proxy_plist.exists()
    
    # Verify only 2 bootstraps (daemon, dashboard)
    bootstraps = [c for c in run.call_args_list if "bootstrap" in c.args[0]]
    assert len(bootstraps) == 2


def test_should_run_proxy_when_agent_flagged(monkeypatch):
    monkeypatch.setattr(svc, "_proxy_unit_file_exists", lambda: False)
    monkeypatch.setattr(
        "memor.config.load_config",
        lambda: {"proxy_agents": {"claude": True}},
    )
    assert svc._should_run_proxy() is True


def test_should_run_proxy_when_unit_file_exists(monkeypatch):
    monkeypatch.setattr(svc, "_proxy_unit_file_exists", lambda: True)
    monkeypatch.setattr("memor.config.load_config", lambda: {"proxy_agents": {}})
    assert svc._should_run_proxy() is True


def test_should_run_proxy_false_by_default(monkeypatch):
    monkeypatch.setattr(svc, "_proxy_unit_file_exists", lambda: False)
    monkeypatch.setattr("memor.config.load_config", lambda: {"proxy_agents": {}})
    assert svc._should_run_proxy() is False


def test_install_infers_proxy_from_should_run(monkeypatch, tmp_path):
    """Default install() must bootstrap proxy when _should_run_proxy()."""
    monkeypatch.setattr(svc, "_is_macos", lambda: True)
    monkeypatch.setattr(svc, "_find_memor_bin", lambda: "/bin/memor")
    monkeypatch.setattr(svc, "PLIST_DIR", tmp_path)
    monkeypatch.setattr(svc, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(svc, "_port_in_use", lambda port: False)
    monkeypatch.setattr(svc, "_should_run_proxy", lambda: True)
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(svc.subprocess, "run", run)

    svc.install(with_dashboard=True, with_proxy=False)

    proxy_plist = tmp_path / f"{svc.PROXY_LABEL}.plist"
    assert proxy_plist.exists()
    bootstraps = [c for c in run.call_args_list if "bootstrap" in c.args[0]]
    assert len(bootstraps) == 3


def test_restart_rebootstraps_proxy_when_opted_in(monkeypatch, tmp_path):
    """restart() must not drop the proxy after stop+install."""
    monkeypatch.setattr(svc, "_is_macos", lambda: True)
    monkeypatch.setattr(svc, "_find_memor_bin", lambda: "/bin/memor")
    monkeypatch.setattr(svc, "PLIST_DIR", tmp_path)
    monkeypatch.setattr(svc, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(svc, "_port_in_use", lambda port: False)
    # After stop, plist still exists → inference keeps proxy.
    (tmp_path / f"{svc.PROXY_LABEL}.plist").write_text("existing")
    monkeypatch.setattr(
        "memor.config.load_config",
        lambda: {"proxy_agents": {"claude": True}},
    )
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(svc.subprocess, "run", run)

    svc.restart()

    bootstraps = [c for c in run.call_args_list if "bootstrap" in c.args[0]]
    assert len(bootstraps) == 3
    assert (tmp_path / f"{svc.PROXY_LABEL}.plist").exists()


def test_uninstall_removes_proxy_plist(monkeypatch, tmp_path):
    """Test that uninstall removes the proxy plist."""
    monkeypatch.setattr(svc, "_is_macos", lambda: True)
    monkeypatch.setattr(svc, "PLIST_DIR", tmp_path)
    monkeypatch.setattr("memor.config.load_config", lambda: {"proxy_agents": {}})
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(svc.subprocess, "run", run)
    
    # Create proxy plist
    proxy_plist = tmp_path / f"{svc.PROXY_LABEL}.plist"
    proxy_plist.write_text("test")
    
    out = svc.uninstall()
    
    # Verify proxy plist was removed
    assert not proxy_plist.exists()
    assert str(proxy_plist) in out


def test_stop_warns_when_proxy_agents_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "_is_macos", lambda: True)
    monkeypatch.setattr(svc, "PLIST_DIR", tmp_path)
    monkeypatch.setattr(svc, "_proxy_port", lambda: 8421)
    monkeypatch.setattr(
        "memor.config.load_config",
        lambda: {"proxy_agents": {"claude": True}},
    )
    (tmp_path / f"{svc.DAEMON_LABEL}.plist").write_text("x")
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(svc.subprocess, "run", run)

    out = svc.stop()
    assert "warning: proxy-enabled agents still point at http://127.0.0.1:8421" in out
    assert "memor service restart" in out


def test_uninstall_failovers_proxy_agents(monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "_is_macos", lambda: True)
    monkeypatch.setattr(svc, "PLIST_DIR", tmp_path)
    monkeypatch.setattr(
        "memor.config.load_config",
        lambda: {"proxy_agents": {"claude": True}},
    )
    called = {}

    def fake_failover(reason=""):
        called["reason"] = reason
        return ["claude: restored config from backup; proxy flag cleared"]

    monkeypatch.setattr(
        "memor.proxy.install.failover_proxy_agents", fake_failover)
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(svc.subprocess, "run", run)
    (tmp_path / f"{svc.PROXY_LABEL}.plist").write_text("x")

    out = svc.uninstall()
    assert "restored config" in out
    assert "service uninstall" in called.get("reason", "")


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
