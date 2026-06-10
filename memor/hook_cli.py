#!/usr/bin/env python3
"""Memor recall hook — works with Claude Code, Codex, and Copilot.

Tries to connect to the warm sidecar at ~/.memor/hook.sock.
Falls back to inline execution if sidecar is unavailable.
"""
from __future__ import annotations
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

SOCK_PATH = Path.home() / ".memor" / "hook.sock"
PID_PATH = Path.home() / ".memor" / "hook.pid"


def _send_to_sidecar(request: dict) -> dict | None:
    if not SOCK_PATH.exists():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(str(SOCK_PATH))
        sock.sendall(json.dumps(request).encode())
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
        sock.close()
        return json.loads(b"".join(chunks))
    except (ConnectionRefusedError, FileNotFoundError, TimeoutError, OSError):
        return None


def _start_sidecar() -> bool:
    subprocess.Popen(
        [sys.executable, "-m", "memor.hook_server"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(30):
        time.sleep(0.1)
        if SOCK_PATH.exists():
            return True
    return False


def _inline_fallback(request: dict) -> dict:
    try:
        from memor.hook_server import handle_request
        return handle_request(request)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        from memor.hook_server import detect_agent, format_hook_response
        return format_hook_response(detect_agent(request), "")


def main():
    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(1)

    result = _send_to_sidecar(request)
    if result is None:
        if _start_sidecar():
            result = _send_to_sidecar(request)
    if result is None:
        result = _inline_fallback(request)

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
