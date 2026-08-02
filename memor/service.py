"""Manage memor background services (daemon + dashboard) as launchd (macOS)
or systemd (Linux) user services.

Two units are managed together so they cycle in lockstep:
  - daemon    (ai.memor.daemon)    — ingest + distill
  - dashboard (ai.memor.dashboard) — web UI on MEMOR_DASHBOARD_PORT (default 8420)

install/stop/uninstall/status/restart all operate on both units, so stopping or
reinstalling recycles the dashboard alongside the daemon. `install(with_dashboard=False)`
installs the daemon only.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import textwrap
from pathlib import Path

DAEMON_LABEL = "ai.memor.daemon"
DASHBOARD_LABEL = "ai.memor.dashboard"
PROXY_LABEL = "ai.memor.proxy"
CURSOR_WIRE_LABEL = "ai.memor.cursor-wire"
# Back-compat alias.
LABEL = DAEMON_LABEL

STATE_DIR = Path.home() / ".memor"
DAEMON_LOG = STATE_DIR / "daemon.log"
DASHBOARD_LOG = STATE_DIR / "dashboard.log"
PROXY_LOG = STATE_DIR / "proxy.log"
CURSOR_WIRE_LOG = STATE_DIR / "cursor-wire.log"
LOG_FILE = DAEMON_LOG  # back-compat alias

# macOS
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
# Linux
SYSTEMD_DIR = Path.home() / ".config" / "systemd" / "user"

DEFAULT_DASHBOARD_PORT = 8420


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _find_memor_bin() -> str:
    """Resolve memor for launchd/systemd. Prefer pipx over a dev-repo .venv."""
    pipx_bin = Path.home() / ".local" / "bin" / "memor"
    if pipx_bin.is_file():
        return str(pipx_bin.resolve())
    path = shutil.which("memor")
    if path and "/.venv/" not in path:
        return path
    # Fall back to any memor on PATH (e.g. active venv during development).
    if path:
        return path
    raise FileNotFoundError(
        "'memor' not found on PATH. Reinstall with: pipx install memor-cli"
    )


def _dashboard_port() -> int:
    try:
        p = int(os.environ.get("MEMOR_DASHBOARD_PORT", str(DEFAULT_DASHBOARD_PORT)))
        return p if p > 0 else DEFAULT_DASHBOARD_PORT
    except (ValueError, TypeError):
        return DEFAULT_DASHBOARD_PORT


def _proxy_port() -> int:
    from memor.config import proxy_port
    return proxy_port()


def _proxy_unit_file_exists() -> bool:
    """True if a proxy launchd plist or systemd unit file is on disk."""
    if _is_macos():
        return _plist_path(PROXY_LABEL).exists()
    return _unit_path("memor-proxy").exists()


def _should_run_proxy() -> bool:
    """Whether install/restart should manage the proxy unit.

    True if any agent opted in via config, or a proxy unit file already exists
    (so upgrades don't leave a stranded plist/unit unloaded).
    """
    from memor.config import load_config
    agents = load_config().get("proxy_agents") or {}
    if any(agents.values()):
        return True
    return _proxy_unit_file_exists()


def _cursor_wire_unit_file_exists() -> bool:
    if _is_macos():
        return _plist_path(CURSOR_WIRE_LABEL).exists()
    return _unit_path("memor-cursor-wire").exists()


def _should_run_cursor_wire() -> bool:
    """Whether install/restart should manage the Cursor wire mitmdump unit."""
    from memor.config import is_cursor_wire_enabled

    if is_cursor_wire_enabled():
        return True
    return _cursor_wire_unit_file_exists()


def _cursor_wire_port() -> int:
    from memor.config import cursor_wire_port

    return cursor_wire_port()


def _units(
    memor_bin: str,
    *,
    with_dashboard: bool = True,
    with_proxy: bool = False,
    with_cursor_wire: bool = False,
    port: int | None = None,
) -> list[dict]:
    """Describe the services to manage. Each entry has the launchd label,
    systemd unit name, program args (after the memor binary), and log file."""
    if port is None:
        port = _dashboard_port()
    proxy_p = _proxy_port()
    units = [{
        "key": "daemon",
        "label": DAEMON_LABEL,
        "systemd_name": "memor-daemon",
        "description": "Memor daemon — memory layer for coding agents",
        "args": [memor_bin, "daemon"],
        "log": DAEMON_LOG,
    }]
    if with_dashboard:
        units.append({
            "key": "dashboard",
            "label": DASHBOARD_LABEL,
            "systemd_name": "memor-dashboard",
            "description": "Memor dashboard — web UI for memory metrics",
            "args": [memor_bin, "dashboard", "--port", str(port), "--no-open"],
            "log": DASHBOARD_LOG,
        })
    if with_proxy:
        units.append({
            "key": "proxy",
            "label": PROXY_LABEL,
            "systemd_name": "memor-proxy",
            "description": "Memor proxy — context compression for AI agents",
            "args": [memor_bin, "proxy", "--port", str(proxy_p)],
            "log": PROXY_LOG,
        })
    if with_cursor_wire:
        wport = _cursor_wire_port()
        units.append({
            "key": "cursor-wire",
            "label": CURSOR_WIRE_LABEL,
            "systemd_name": "memor-cursor-wire",
            "description": "Memor Cursor wire — mitmdump subscription compression",
            "args": [
                memor_bin, "cursor-wire-mitm", "--dump", "--port", str(wport),
            ],
            "log": CURSOR_WIRE_LOG,
        })
    return units


def _all_unit_labels() -> list[tuple[str, str]]:
    """(launchd label, systemd name) for every unit we might have installed,
    used by uninstall/stop/status which must act regardless of with_dashboard/with_proxy."""
    return [
        (DAEMON_LABEL, "memor-daemon"),
        (DASHBOARD_LABEL, "memor-dashboard"),
        (PROXY_LABEL, "memor-proxy"),
        (CURSOR_WIRE_LABEL, "memor-cursor-wire"),
    ]


def _plist_path(label: str) -> Path:
    return PLIST_DIR / f"{label}.plist"


def _unit_path(systemd_name: str) -> Path:
    return SYSTEMD_DIR / f"{systemd_name}.service"


def _plist_content(label: str, args, log_file) -> str:
    if isinstance(args, str):  # back-compat: old callers passed just the memor bin
        args = [args, "daemon"]
    arg_xml = "\n".join(f"                <string>{a}</string>" for a in args)
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{label}</string>
            <key>ProgramArguments</key>
            <array>
{arg_xml}
            </array>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <true/>
            <key>StandardOutPath</key>
            <string>{log_file}</string>
            <key>StandardErrorPath</key>
            <string>{log_file}</string>
            <key>ProcessType</key>
            <string>Background</string>
        </dict>
        </plist>
    """)


def _systemd_unit(label, description="Memor daemon — memory layer for coding agents",
                  args=None, log_file=LOG_FILE) -> str:
    if args is None:  # back-compat: old signature was _systemd_unit(memor_bin)
        args = [label, "daemon"]
    exec_start = " ".join(str(a) for a in args)
    return textwrap.dedent(f"""\
        [Unit]
        Description={description}
        After=default.target

        [Service]
        Type=simple
        ExecStart={exec_start}
        Restart=on-failure
        RestartSec=10
        StandardOutput=append:{log_file}
        StandardError=append:{log_file}

        [Install]
        WantedBy=default.target
    """)


def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _port_held_by_memor(port: int) -> bool:
    """True if something already listening looks like our memor service."""
    try:
        r = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return False
    pids = [p for p in (r.stdout or "").split() if p.isdigit()]
    for pid in pids:
        try:
            ps = subprocess.run(
                ["ps", "-p", pid, "-o", "args="],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except Exception:
            continue
        args = (ps.stdout or "").lower()
        if "memor" in args or "mitmdump" in args or "cursor-wire" in args:
            return True
    return False


def install(with_dashboard: bool = True, with_proxy: bool = False,
            with_cursor_wire: bool | None = None) -> str:
    """Install background services. The proxy is opt-in via `memor install-proxy`.

    When `with_proxy` is False (default), it is still enabled if `_should_run_proxy()`
    — so `memor service restart` after upgrade keeps a previously installed proxy.
    Cursor wire mitmdump follows `cursor_wire` config the same way.
    """
    if not with_proxy and _should_run_proxy():
        with_proxy = True
    # None = follow config/existing unit. Explicit False must win (disable-cursor-wire).
    if with_cursor_wire is None:
        with_cursor_wire = _should_run_cursor_wire()

    # Stop any existing wire unit before port pick so we don't treat ourselves as busy.
    notes: list[str] = []
    if with_cursor_wire:
        try:
            if _is_macos():
                wpath = _plist_path(CURSOR_WIRE_LABEL)
                if wpath.exists():
                    subprocess.run(
                        ["launchctl", "bootout", f"gui/{os.getuid()}", str(wpath)],
                        capture_output=True,
                    )
            else:
                if _unit_path("memor-cursor-wire").exists():
                    subprocess.run(
                        ["systemctl", "--user", "stop", "memor-cursor-wire"],
                        capture_output=True,
                    )
            import time as _time
            _time.sleep(0.3)

            from memor.config import is_cursor_wire_enabled, set_cursor_wire_port
            from memor.cursor_wire.ports import resolve_wire_port
            from memor.cursor_wire.settings import write_cursor_wire_settings

            preferred = _cursor_wire_port()
            port_pick, notes = resolve_wire_port(preferred=preferred)
            if port_pick != preferred:
                set_cursor_wire_port(port_pick)
                if is_cursor_wire_enabled():
                    try:
                        write_cursor_wire_settings(port_pick)
                    except Exception:
                        pass
        except Exception:
            notes = []

    memor_bin = _find_memor_bin()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    port = _dashboard_port()
    units = _units(
        memor_bin,
        with_dashboard=with_dashboard,
        with_proxy=with_proxy,
        with_cursor_wire=with_cursor_wire,
        port=port,
    )

    warnings = list(notes) if notes else []
    # Only warn when a *foreign* process holds the port. Our own running
    # dashboard/proxy (common on reinstall) is fine — launchctl will recycle it.
    if with_dashboard and _port_in_use(port) and not _port_held_by_memor(port):
        warnings.append(
            f"  warning: port {port} is already in use — the dashboard service may "
            f"crash-loop. Stop the other process or set MEMOR_DASHBOARD_PORT.")
    if with_proxy:
        pport = _proxy_port()
        if _port_in_use(pport) and not _port_held_by_memor(pport):
            warnings.append(
                f"  warning: port {pport} is already in use — the proxy service may "
                f"crash-loop. Stop the other process or change proxy.port in "
                f"~/.memor/config.json.")

    lines = []
    if _is_macos():
        PLIST_DIR.mkdir(parents=True, exist_ok=True)
        for u in units:
            path = _plist_path(u["label"])
            path.write_text(_plist_content(u["label"], u["args"], u["log"]))
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)],
                           capture_output=True)
            subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
                           check=True)
            lines.append(f"  {u['key']}: {path}")
        header = "Installed and started launchd services:"
    else:
        SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
        for u in units:
            path = _unit_path(u["systemd_name"])
            path.write_text(_systemd_unit(u["label"], u["description"], u["args"], u["log"]))
            lines.append(f"  {u['key']}: {path}")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        for u in units:
            subprocess.run(["systemctl", "--user", "enable", "--now", u["systemd_name"]],
                           check=True)
        header = "Installed and started systemd user services:"

    out = [header, *lines]
    if with_dashboard:
        out.append(f"  dashboard: http://localhost:{port}")
    if with_cursor_wire:
        out.append(f"  cursor-wire: http://127.0.0.1:{_cursor_wire_port()}")
    if warnings:
        out.extend(warnings)
    return "\n".join(out)


def uninstall() -> str:
    from memor.config import is_cursor_wire_enabled, load_config
    from memor.proxy.install import failover_proxy_agents

    failover_lines: list[str] = []
    agents = load_config().get("proxy_agents") or {}
    if any(agents.values()):
        failover_lines = failover_proxy_agents(
            "service uninstall — restoring direct API endpoints")

    wire_lines: list[str] = []
    if is_cursor_wire_enabled() or _cursor_wire_unit_file_exists():
        from memor.cursor_wire.settings import failover_cursor_wire

        wire_lines = failover_cursor_wire(
            "service uninstall — removed Cursor http.proxy wire keys"
        )

    removed = []
    if _is_macos():
        for label, _ in _all_unit_labels():
            path = _plist_path(label)
            if path.exists():
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)],
                               capture_output=True)
                path.unlink()
                removed.append(str(path))
    else:
        changed = False
        for _, name in _all_unit_labels():
            path = _unit_path(name)
            if path.exists():
                subprocess.run(["systemctl", "--user", "disable", "--now", name],
                               capture_output=True)
                path.unlink()
                removed.append(str(path))
                changed = True
        if changed:
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    if not removed and not failover_lines and not wire_lines:
        return "No services installed."
    parts = []
    if removed:
        parts.append(
            "Services stopped and removed:\n" + "\n".join(f"  deleted: {p}" for p in removed)
        )
    elif not removed:
        parts.append("No service unit files to remove.")
    if failover_lines:
        parts.append("Proxy agent configs:\n" + "\n".join(f"  {ln}" for ln in failover_lines))
    if wire_lines:
        parts.append("Cursor wire:\n" + "\n".join(f"  {ln}" for ln in wire_lines))
        parts.append(
            "  Note: remove mitmproxy CA from Keychain if you no longer need wire MITM."
        )
    return "\n".join(parts)


def stop() -> str:
    from memor.config import is_cursor_wire_enabled, load_config

    stopped = []
    if _is_macos():
        for label, _ in _all_unit_labels():
            path = _plist_path(label)
            if path.exists():
                r = subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)],
                                   capture_output=True, text=True)
                stopped.append(label if r.returncode == 0 else f"{label} (failed)")
    else:
        for _, name in _all_unit_labels():
            if _unit_path(name).exists():
                r = subprocess.run(["systemctl", "--user", "stop", name],
                                   capture_output=True, text=True)
                stopped.append(name if r.returncode == 0 else f"{name} (failed)")
    if not stopped:
        return "No services installed. Nothing to stop."
    out = ("Stopped: " + ", ".join(stopped) +
           "\n  They restart on next login. To remove: memor service uninstall")
    agents = load_config().get("proxy_agents") or {}
    if any(agents.values()):
        pport = _proxy_port()
        out += (
            f"\n  warning: proxy-enabled agents still point at http://127.0.0.1:{pport}."
            f"\n    Start again: memor service restart"
            f"\n    Or restore direct API: memor uninstall-proxy --agent <claude|codex>"
        )
    # Strip wire proxy keys so Composer is not left on a dead mitmdump port.
    # Keep cursor_wire=true so `memor service restart` brings the unit back.
    if is_cursor_wire_enabled() or _cursor_wire_unit_file_exists():
        from memor.cursor_wire.settings import strip_cursor_wire_settings

        out += "\n  " + strip_cursor_wire_settings()
        out += (
            "\n  warning: Cursor wire http.proxy removed while services are stopped."
            "\n    Run: memor service restart  (re-applies wire settings when healthy)"
        )
    return out


def restart() -> str:
    """Stop and reinstall units — use after `pipx upgrade` to recycle them
    onto the new binary (the running processes keep old code until restarted)."""
    from memor.config import is_cursor_wire_enabled

    stop_msg = stop()
    install_msg = install()
    parts = [stop_msg, install_msg]
    if is_cursor_wire_enabled() or _should_run_cursor_wire():
        from memor.cursor_wire.ports import wait_for_wire_health
        from memor.cursor_wire.settings import failover_cursor_wire, is_memor_wire_proxy
        from memor.proxy.cursor_install import cursor_paths
        from memor.proxy.vscode_settings import load_settings_json

        wport = _cursor_wire_port()
        ok, detail = wait_for_wire_health(wport)
        if not ok:
            parts.extend(failover_cursor_wire(detail))
        else:
            # Do NOT force-write Cursor http.proxy on restart. If the user
            # removed/commented proxy keys (Composer broken), leave them alone.
            # Only install-proxy --wire writes settings.
            try:
                settings_path, _, _ = cursor_paths()
                settings = load_settings_json(settings_path)
                proxy = settings.get("http.proxy")
                if is_memor_wire_proxy(str(proxy) if proxy is not None else None):
                    parts.append(
                        f"Cursor wire: healthy on :{wport}; existing http.proxy left as-is"
                    )
                else:
                    parts.append(
                        f"Cursor wire: mitmdump healthy on :{wport}, but Cursor "
                        f"http.proxy is not set — not re-applying (run "
                        f"`memor install-proxy --agent cursor --wire` to enable)"
                    )
            except Exception as exc:
                parts.append(
                    f"Cursor wire: healthy on :{wport}; settings check skipped ({exc})"
                )
    return "\n".join(parts)


def _macos_unit_status(label: str) -> str:
    path = _plist_path(label)
    if not path.exists():
        return "not installed"
    r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return "installed, not running"
    for line in r.stdout.splitlines():
        s = line.strip()
        if s.startswith("pid ="):
            return f"running (pid {s.split('=')[1].strip()})"
    return "running"


def _linux_unit_status(name: str) -> str:
    if not _unit_path(name).exists():
        return "not installed"
    r = subprocess.run(["systemctl", "--user", "is-active", name],
                       capture_output=True, text=True)
    state = r.stdout.strip()
    if state == "active":
        pid_r = subprocess.run(["systemctl", "--user", "show", "-p", "MainPID", name],
                               capture_output=True, text=True)
        return f"running (pid {pid_r.stdout.strip().split('=')[-1]})"
    return f"installed, {state}"


def status() -> str:
    rows = []
    for label, name in _all_unit_labels():
        if label == DAEMON_LABEL:
            key = "daemon"
        elif label == DASHBOARD_LABEL:
            key = "dashboard"
        elif label == PROXY_LABEL:
            key = "proxy"
        elif label == CURSOR_WIRE_LABEL:
            key = "cursor-wire"
        else:
            key = "unknown"
        st = _macos_unit_status(label) if _is_macos() else _linux_unit_status(name)
        if key == "dashboard" and st.startswith("running"):
            st += f" → http://localhost:{_dashboard_port()}"
        elif key == "proxy" and st.startswith("running"):
            st += f" → http://localhost:{_proxy_port()}"
        elif key == "cursor-wire" and st.startswith("running"):
            st += f" → http://127.0.0.1:{_cursor_wire_port()}"
        rows.append(f"  {key}: {st}")
    if all("not installed" in r for r in rows):
        return "Not installed. Run: memor service install"
    return "Service status:\n" + "\n".join(rows)
