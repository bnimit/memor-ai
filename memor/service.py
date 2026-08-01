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
# Back-compat alias.
LABEL = DAEMON_LABEL

STATE_DIR = Path.home() / ".memor"
DAEMON_LOG = STATE_DIR / "daemon.log"
DASHBOARD_LOG = STATE_DIR / "dashboard.log"
PROXY_LOG = STATE_DIR / "proxy.log"
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


def _units(memor_bin: str, *, with_dashboard: bool = True, with_proxy: bool = False, port: int | None = None) -> list[dict]:
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
    return units


def _all_unit_labels() -> list[tuple[str, str]]:
    """(launchd label, systemd name) for every unit we might have installed,
    used by uninstall/stop/status which must act regardless of with_dashboard/with_proxy."""
    return [
        (DAEMON_LABEL, "memor-daemon"),
        (DASHBOARD_LABEL, "memor-dashboard"),
        (PROXY_LABEL, "memor-proxy"),
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


def install(with_dashboard: bool = True, with_proxy: bool = False) -> str:
    """Install background services. The proxy is opt-in via `memor install-proxy`.

    When `with_proxy` is False (default), it is still enabled if `_should_run_proxy()`
    — so `memor service restart` after upgrade keeps a previously installed proxy.
    """
    if not with_proxy and _should_run_proxy():
        with_proxy = True
    memor_bin = _find_memor_bin()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    port = _dashboard_port()
    units = _units(memor_bin, with_dashboard=with_dashboard, with_proxy=with_proxy, port=port)

    warnings = []
    if with_dashboard and _port_in_use(port):
        warnings.append(
            f"  warning: port {port} is already in use — the dashboard service may "
            f"crash-loop. Stop the other process or set MEMOR_DASHBOARD_PORT.")
    if with_proxy:
        pport = _proxy_port()
        if _port_in_use(pport):
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
    if warnings:
        out.extend(warnings)
    return "\n".join(out)


def uninstall() -> str:
    from memor.config import load_config
    from memor.proxy.install import failover_proxy_agents

    failover_lines: list[str] = []
    agents = load_config().get("proxy_agents") or {}
    if any(agents.values()):
        failover_lines = failover_proxy_agents(
            "service uninstall — restoring direct API endpoints")

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
    if not removed and not failover_lines:
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
    return "\n".join(parts)


def stop() -> str:
    from memor.config import load_config

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
    return out


def restart() -> str:
    """Stop and reinstall both units — use after `pipx upgrade` to recycle them
    onto the new binary (the running processes keep old code until restarted)."""
    stop()
    return install()


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
        else:
            key = "unknown"
        st = _macos_unit_status(label) if _is_macos() else _linux_unit_status(name)
        if key == "dashboard" and st.startswith("running"):
            st += f" → http://localhost:{_dashboard_port()}"
        elif key == "proxy" and st.startswith("running"):
            st += f" → http://localhost:{_proxy_port()}"
        rows.append(f"  {key}: {st}")
    if all("not installed" in r for r in rows):
        return "Not installed. Run: memor service install"
    return "Service status:\n" + "\n".join(rows)
