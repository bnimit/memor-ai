"""Cursor full-install helpers: ports, settings, config flags."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from memor.config import (
    is_cursor_wire_enabled,
    load_config,
    set_cursor_wire,
    set_cursor_wire_port,
    cursor_wire_port,
)
from memor.cursor_wire.ports import port_in_use, resolve_wire_port
from memor.cursor_wire.settings import (
    failover_cursor_wire,
    memor_wire_proxy_url,
    strip_cursor_wire_settings,
    wire_settings_updates,
    write_cursor_wire_settings,
)


def test_resolve_wire_port_prefers_free_default(monkeypatch):
    monkeypatch.delenv("MEMOR_CURSOR_WIRE_PORT", raising=False)
    with patch("memor.cursor_wire.ports.port_in_use", return_value=False):
        port, notes = resolve_wire_port()
    assert port == 8080
    assert notes == []


def test_resolve_wire_port_scans_when_8080_busy(monkeypatch):
    monkeypatch.delenv("MEMOR_CURSOR_WIRE_PORT", raising=False)

    def busy(port, host="127.0.0.1"):
        return port == 8080

    with patch("memor.cursor_wire.ports.port_in_use", side_effect=busy):
        port, notes = resolve_wire_port()
    assert port == 8081
    assert any("8080" in n for n in notes)


def test_resolve_wire_port_env_busy_raises(monkeypatch):
    monkeypatch.setenv("MEMOR_CURSOR_WIRE_PORT", "8123")
    with patch("memor.cursor_wire.ports.port_in_use", return_value=True):
        try:
            resolve_wire_port()
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "8123" in str(exc)


def test_wire_settings_round_trip(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}\n")
    backup = tmp_path / "backup.json"
    monkeypatch.setattr(
        "memor.cursor_wire.settings.cursor_paths",
        lambda: (settings_path, backup, "{}\n"),
    )
    write_cursor_wire_settings(8082)
    data = json.loads(settings_path.read_text())
    assert data["http.proxy"] == memor_wire_proxy_url(8082)
    assert data["http.proxySupport"] == "override"
    assert data["http.proxyStrictSSL"] is False
    assert "127.0.0.1" in data["http.noProxy"]
    assert data["cursor.general.disableHttp2"] is True

    msg = strip_cursor_wire_settings()
    assert "removed" in msg
    data2 = json.loads(settings_path.read_text())
    assert "http.proxy" not in data2


def test_failover_clears_flag(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr("memor.config.CONFIG_PATH", cfg)
    monkeypatch.setattr("memor.config.STATE_DIR", tmp_path)
    set_cursor_wire(True)
    set_cursor_wire_port(8080)
    assert is_cursor_wire_enabled()

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(wire_settings_updates(8080)))
    backup = tmp_path / "backup.json"
    monkeypatch.setattr(
        "memor.cursor_wire.settings.cursor_paths",
        lambda: (settings_path, backup, "{}\n"),
    )
    lines = failover_cursor_wire("test")
    assert any("failover" in ln for ln in lines)
    assert is_cursor_wire_enabled() is False


def test_cursor_wire_config_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr("memor.config.CONFIG_PATH", cfg)
    monkeypatch.setattr("memor.config.STATE_DIR", tmp_path)
    monkeypatch.delenv("MEMOR_CURSOR_WIRE_PORT", raising=False)
    data = load_config()
    assert data.get("cursor_wire") is False
    assert cursor_wire_port() == 8080


def test_proxy_status_includes_cursor_wire(tmp_path):
    from fastapi.testclient import TestClient
    from memor.dashboard.server import create_app
    from memor.store.sqlite_store import SqliteStore

    db = str(tmp_path / "m.db")
    SqliteStore(db, dim=16)
    client = TestClient(create_app(db))
    r = client.get("/api/proxy-status")
    assert r.status_code == 200
    data = r.json()
    assert "cursor_wire" in data
    assert "enabled" in data["cursor_wire"]
    assert "healthy" in data["cursor_wire"]
    assert "port" in data["cursor_wire"]


def test_dashboard_html_has_cursor_wire_chip(tmp_path):
    from fastapi.testclient import TestClient
    from memor.dashboard.server import create_app
    from memor.store.sqlite_store import SqliteStore

    db = str(tmp_path / "m.db")
    SqliteStore(db, dim=16)
    html = TestClient(create_app(db)).get("/").text
    assert "chip-cursor-wire" in html
    assert "status-cursor-wire" in html


def test_install_skips_wire_when_declined(tmp_path, monkeypatch):
    from memor.cursor_wire.install import install_cursor_full_stack

    monkeypatch.setattr("memor.config.CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr("memor.config.STATE_DIR", tmp_path)
    monkeypatch.setattr(
        "memor.cursor_wire.install.install_cursor_proxy",
        lambda *a, **k: ["manual"],
    )
    monkeypatch.setattr("memor.cursor_wire.install._ensure_memory_hooks", lambda: [])
    monkeypatch.setattr("memor.cursor_wire.install._ensure_shell_hooks", lambda: [])
    monkeypatch.setattr(
        "memor.service.install",
        lambda **k: "services ok",
    )

    result = install_cursor_full_stack(
        byok_port=8421,
        yes=False,
        confirm=lambda _m: True,
        confirm_wire=lambda _m: False,
    )
    assert result.byok_ok is True
    assert result.wire_enabled is False
    assert any("declined" in ln for ln in result.lines)


def test_install_yes_does_not_enable_wire_without_flag(tmp_path, monkeypatch):
    from memor.cursor_wire.install import install_cursor_full_stack

    monkeypatch.setattr("memor.config.CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr("memor.config.STATE_DIR", tmp_path)
    monkeypatch.setattr(
        "memor.cursor_wire.install.install_cursor_proxy",
        lambda *a, **k: [],
    )
    monkeypatch.setattr("memor.cursor_wire.install._ensure_memory_hooks", lambda: [])
    monkeypatch.setattr("memor.cursor_wire.install._ensure_shell_hooks", lambda: [])
    monkeypatch.setattr("memor.service.install", lambda **k: "services ok")

    result = install_cursor_full_stack(byok_port=8421, yes=True)
    assert result.wire_enabled is False
    assert any("-y does not enable MITM" in ln for ln in result.lines)


def test_ensure_mitmproxy_rejects_brew_only(monkeypatch):
    from memor.cursor_wire import launch as launch_mod

    monkeypatch.setattr(launch_mod, "mitmproxy_importable", lambda: False)
    monkeypatch.setattr(launch_mod, "venv_mitmdump", lambda: None)
    monkeypatch.setattr(launch_mod, "find_mitm_bin", lambda: "/opt/homebrew/bin/mitmdump")
    monkeypatch.setattr(launch_mod.shutil, "which", lambda _n: None)

    class _Proc:
        returncode = 1
        stderr = "no pip"
        stdout = ""

    monkeypatch.setattr(launch_mod.subprocess, "run", lambda *a, **k: _Proc())
    ok, detail = launch_mod.ensure_mitmproxy()
    assert ok is False
    assert "standalone" in detail or "pipx inject" in detail


def test_ensure_mitmproxy_pipx_inject_when_missing(monkeypatch):
    from memor.cursor_wire import launch as launch_mod

    monkeypatch.setattr(launch_mod, "mitmproxy_importable", lambda: False)
    monkeypatch.setattr(launch_mod, "venv_mitmdump", lambda: None)
    monkeypatch.setattr(launch_mod, "find_mitm_bin", lambda: None)
    monkeypatch.setattr(launch_mod.shutil, "which", lambda n: "/usr/bin/pipx" if n == "pipx" else None)

    class _Proc:
        returncode = 0
        stderr = ""
        stdout = ""

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        monkeypatch.setattr(launch_mod, "mitmproxy_importable", lambda: True)
        monkeypatch.setattr(launch_mod, "venv_mitmdump", lambda: "/venv/bin/mitmdump")
        return _Proc()

    monkeypatch.setattr(launch_mod.subprocess, "run", fake_run)
    ok, detail = launch_mod.ensure_mitmproxy()
    assert ok is True
    assert "pipx inject" in detail
    assert calls and calls[0][0].endswith("pipx") and "inject" in calls[0]


def test_venv_mitmdump_uses_sys_prefix_not_framework_resolve(tmp_path, monkeypatch):
    from memor.cursor_wire import launch as launch_mod

    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "mitmdump"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    monkeypatch.setattr(launch_mod.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(launch_mod.sys, "executable", "/opt/homebrew/Cellar/python/Frameworks/Python.app/Contents/MacOS/Python")
    monkeypatch.setattr(
        launch_mod,
        "sysconfig",
        type("SC", (), {"get_path": staticmethod(lambda _k: str(bindir))})(),
        raising=False,
    )
    # Force import of sysconfig path via module attr used in function — patch get_path
    import sysconfig as real_sysconfig

    monkeypatch.setattr(real_sysconfig, "get_path", lambda _k: str(bindir))
    assert launch_mod.venv_mitmdump() == str(script)


def test_build_mitmdump_argv_uses_venv_console_script(monkeypatch):
    from memor.cursor_wire import launch as launch_mod

    monkeypatch.setattr(launch_mod, "mitmproxy_importable", lambda: True)
    monkeypatch.setattr(launch_mod, "venv_mitmdump", lambda: "/venv/bin/mitmdump")
    argv = launch_mod.build_mitmdump_argv(listen_port=8082)
    assert argv[0] == "/venv/bin/mitmdump"
    assert "mitmproxy.tools.dump" not in argv
    assert "regular@8082" in " ".join(argv)


def test_build_mitmdump_argv_fallback_python_c(monkeypatch):
    from memor.cursor_wire import launch as launch_mod

    monkeypatch.setattr(launch_mod, "mitmproxy_importable", lambda: True)
    monkeypatch.setattr(launch_mod, "venv_mitmdump", lambda: None)
    argv = launch_mod.build_mitmdump_argv(listen_port=8082)
    assert argv[0] == launch_mod.sys.executable
    assert argv[1] == "-c"
    assert "mitmproxy.tools.main" in argv[2]
    assert "regular@8082" in " ".join(argv)


def test_should_run_cursor_wire_from_flag(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr("memor.config.CONFIG_PATH", cfg)
    monkeypatch.setattr("memor.config.STATE_DIR", tmp_path)
    set_cursor_wire(True)
    from memor.service import _should_run_cursor_wire

    with patch("memor.service._cursor_wire_unit_file_exists", return_value=False):
        assert _should_run_cursor_wire() is True
    set_cursor_wire(False)
    with patch("memor.service._cursor_wire_unit_file_exists", return_value=False):
        assert _should_run_cursor_wire() is False
