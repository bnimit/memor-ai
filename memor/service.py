"""Manage memor daemon as a launchd (macOS) or systemd (Linux) service."""
from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import textwrap
from pathlib import Path

LABEL = "ai.memor.daemon"
STATE_DIR = Path.home() / ".memor"
PID_FILE = STATE_DIR / "daemon.pid"
LOG_FILE = STATE_DIR / "daemon.log"

# macOS
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = PLIST_DIR / f"{LABEL}.plist"

# Linux
SYSTEMD_DIR = Path.home() / ".config" / "systemd" / "user"
UNIT_PATH = SYSTEMD_DIR / "memor-daemon.service"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _find_memor_bin() -> str:
    path = shutil.which("memor")
    if not path:
        raise FileNotFoundError(
            "'memor' not found on PATH. Reinstall with: pipx install memor-cli"
        )
    return path


def _plist_content(memor_bin: str) -> str:
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{LABEL}</string>
            <key>ProgramArguments</key>
            <array>
                <string>{memor_bin}</string>
                <string>daemon</string>
            </array>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <true/>
            <key>StandardOutPath</key>
            <string>{LOG_FILE}</string>
            <key>StandardErrorPath</key>
            <string>{LOG_FILE}</string>
            <key>ProcessType</key>
            <string>Background</string>
        </dict>
        </plist>
    """)


def _systemd_unit(memor_bin: str) -> str:
    return textwrap.dedent(f"""\
        [Unit]
        Description=Memor daemon — memory layer for coding agents
        After=default.target

        [Service]
        Type=simple
        ExecStart={memor_bin} daemon
        Restart=on-failure
        RestartSec=10
        StandardOutput=append:{LOG_FILE}
        StandardError=append:{LOG_FILE}

        [Install]
        WantedBy=default.target
    """)


def install() -> str:
    memor_bin = _find_memor_bin()
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    if _is_macos():
        PLIST_DIR.mkdir(parents=True, exist_ok=True)
        PLIST_PATH.write_text(_plist_content(memor_bin))
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST_PATH)],
            capture_output=True,
        )
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST_PATH)],
            check=True,
        )
        return f"Installed and started launchd service.\n  plist: {PLIST_PATH}\n  logs:  {LOG_FILE}"
    else:
        SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
        UNIT_PATH.write_text(_systemd_unit(memor_bin))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "memor-daemon"], check=True)
        return f"Installed and started systemd user service.\n  unit: {UNIT_PATH}\n  logs: {LOG_FILE}"


def uninstall() -> str:
    if _is_macos():
        if PLIST_PATH.exists():
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST_PATH)],
                capture_output=True,
            )
            PLIST_PATH.unlink()
            return f"Service stopped and removed.\n  deleted: {PLIST_PATH}"
        return "No service installed."
    else:
        if UNIT_PATH.exists():
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", "memor-daemon"],
                capture_output=True,
            )
            UNIT_PATH.unlink()
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            return f"Service stopped and removed.\n  deleted: {UNIT_PATH}"
        return "No service installed."


def stop() -> str:
    if _is_macos():
        if not PLIST_PATH.exists():
            return "No service installed. Nothing to stop."
        result = subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST_PATH)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return "Service stopped. It will start again on next login.\n  To remove permanently: memor service uninstall"
        return f"Failed to stop service: {result.stderr.strip()}"
    else:
        result = subprocess.run(
            ["systemctl", "--user", "stop", "memor-daemon"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return "Service stopped. It will start again on next login.\n  To remove permanently: memor service uninstall"
        return f"Failed to stop service: {result.stderr.strip()}"


def status() -> str:
    if _is_macos():
        if not PLIST_PATH.exists():
            return "Not installed. Run: memor service install"
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return "Installed but not running.\n  Start with: memor service install"
        pid = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("pid ="):
                pid = stripped.split("=")[1].strip()
                break
        running = f"Running (pid {pid})" if pid else "Running"
        return f"{running}\n  plist: {PLIST_PATH}\n  logs:  {LOG_FILE}"
    else:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "memor-daemon"],
            capture_output=True, text=True,
        )
        state = result.stdout.strip()
        if state == "active":
            pid_result = subprocess.run(
                ["systemctl", "--user", "show", "-p", "MainPID", "memor-daemon"],
                capture_output=True, text=True,
            )
            pid = pid_result.stdout.strip().split("=")[-1]
            return f"Running (pid {pid})\n  unit: {UNIT_PATH}\n  logs: {LOG_FILE}"
        if UNIT_PATH.exists():
            return f"Installed but {state}.\n  Start with: systemctl --user start memor-daemon"
        return "Not installed. Run: memor service install"
