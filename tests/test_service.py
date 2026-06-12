import platform
from unittest.mock import MagicMock

import memor.service as svc
from memor.service import (
    _plist_content, _systemd_unit, _units, _dashboard_port,
    DAEMON_LABEL, DASHBOARD_LABEL, LOG_FILE,
)


# ---- content builders (back-compat single-arg + new parametrized) ----

def test_plist_content_backcompat():
    plist = _plist_content("/usr/local/bin/memor", ["/usr/local/bin/memor", "daemon"], LOG_FILE)
    assert "/usr/local/bin/memor" in plist
    assert "<string>daemon</string>" in plist
    assert "<key>KeepAlive</key>" in plist
    assert str(LOG_FILE) in plist


def test_plist_content_dashboard_args():
    plist = _plist_content(DASHBOARD_LABEL,
                           ["/bin/memor", "dashboard", "--port", "8420"],
                           svc.DASHBOARD_LOG)
    assert f"<string>{DASHBOARD_LABEL}</string>" in plist
    assert "<string>dashboard</string>" in plist
    assert "<string>8420</string>" in plist
    assert str(svc.DASHBOARD_LOG) in plist


def test_systemd_unit_backcompat():
    unit = _systemd_unit("/usr/local/bin/memor")
    assert "/usr/local/bin/memor daemon" in unit
    assert "Restart=on-failure" in unit
    assert str(LOG_FILE) in unit


def test_systemd_unit_dashboard():
    unit = _systemd_unit(DASHBOARD_LABEL, "Memor dashboard",
                         ["/bin/memor", "dashboard", "--port", "8420"], svc.DASHBOARD_LOG)
    assert "ExecStart=/bin/memor dashboard --port 8420" in unit
    assert "Description=Memor dashboard" in unit


def test_is_macos():
    assert svc._is_macos() == (platform.system() == "Darwin")


# ---- units + port ----

def test_units_includes_dashboard_by_default():
    units = _units("/bin/memor", with_dashboard=True, port=8420)
    keys = {u["key"] for u in units}
    assert keys == {"daemon", "dashboard"}
    dash = next(u for u in units if u["key"] == "dashboard")
    assert "dashboard" in dash["args"] and "8420" in dash["args"]
    daemon = next(u for u in units if u["key"] == "daemon")
    assert daemon["args"][-1] == "daemon"


def test_units_no_dashboard():
    units = _units("/bin/memor", with_dashboard=False, port=8420)
    assert {u["key"] for u in units} == {"daemon"}


def test_dashboard_port_default(monkeypatch):
    monkeypatch.delenv("MEMOR_DASHBOARD_PORT", raising=False)
    assert _dashboard_port() == 8420


def test_dashboard_port_env(monkeypatch):
    monkeypatch.setenv("MEMOR_DASHBOARD_PORT", "9123")
    assert _dashboard_port() == 9123


def test_dashboard_port_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MEMOR_DASHBOARD_PORT", "notaport")
    assert _dashboard_port() == 8420


# ---- install / uninstall on macOS (mocked) ----

def _macos_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "_is_macos", lambda: True)
    monkeypatch.setattr(svc, "_find_memor_bin", lambda: "/bin/memor")
    monkeypatch.setattr(svc, "PLIST_DIR", tmp_path)
    monkeypatch.setattr(svc, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(svc, "_port_in_use", lambda port: False)
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(svc.subprocess, "run", run)
    return run


def test_install_writes_both_units(monkeypatch, tmp_path):
    run = _macos_setup(monkeypatch, tmp_path)
    out = svc.install(with_dashboard=True)
    assert (tmp_path / f"{DAEMON_LABEL}.plist").exists()
    assert (tmp_path / f"{DASHBOARD_LABEL}.plist").exists()
    dash = (tmp_path / f"{DASHBOARD_LABEL}.plist").read_text()
    assert "<string>dashboard</string>" in dash
    bootstraps = [c for c in run.call_args_list if "bootstrap" in c.args[0]]
    assert len(bootstraps) == 2
    assert "dashboard" in out


def test_install_no_dashboard_writes_only_daemon(monkeypatch, tmp_path):
    _macos_setup(monkeypatch, tmp_path)
    svc.install(with_dashboard=False)
    assert (tmp_path / f"{DAEMON_LABEL}.plist").exists()
    assert not (tmp_path / f"{DASHBOARD_LABEL}.plist").exists()


def test_uninstall_removes_both(monkeypatch, tmp_path):
    _macos_setup(monkeypatch, tmp_path)
    (tmp_path / f"{DAEMON_LABEL}.plist").write_text("x")
    (tmp_path / f"{DASHBOARD_LABEL}.plist").write_text("x")
    out = svc.uninstall()
    assert not (tmp_path / f"{DAEMON_LABEL}.plist").exists()
    assert not (tmp_path / f"{DASHBOARD_LABEL}.plist").exists()
    assert "removed" in out.lower()


def test_uninstall_nothing_installed(monkeypatch, tmp_path):
    _macos_setup(monkeypatch, tmp_path)
    assert svc.uninstall() == "No services installed."
