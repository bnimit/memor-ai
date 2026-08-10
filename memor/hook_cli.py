#!/usr/bin/env python3
"""Memor recall hook — Claude Code, Cursor, Codex, Copilot, Kimi, Goose.

Tries to connect to the warm sidecar at ~/.memor/hook.sock.
Falls back to inline execution if sidecar is unavailable.
"""
from __future__ import annotations
import json
import os
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


#: Longest a prompt will wait for a freshly spawned sidecar. Measured cold
#: starts land at 532-822 ms, so 3 s clears them with headroom. Shortening it
#: was tried and made things *worse*: the wait almost always pays off, and
#: giving up early forces the inline path, which costs ~980 ms against ~130 ms
#: for a warm socket.
_SIDECAR_BOOT_BUDGET_S = 3.0

#: How often to check for the socket. The original 100 ms tick meant a sidecar
#: ready at 532 ms was not noticed until 600 ms, and the granularity is pure
#: latency: nothing else happens between checks.
_SIDECAR_POLL_S = 0.01


def _start_sidecar(budget: float = _SIDECAR_BOOT_BUDGET_S) -> bool:
    """Spawn the sidecar and wait, briefly, for it to accept connections.

    The process is detached, so returning False does not abandon it: it keeps
    starting and serves the next prompt. Only this prompt falls back inline.
    """
    subprocess.Popen(
        [sys.executable, "-m", "memor.hook_server"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if SOCK_PATH.exists():
            return True
        time.sleep(_SIDECAR_POLL_S)
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

    # install-hook stamps MEMOR_HOOK_AGENT=kimi|goose into the command so
    # Claude-shaped Kimi payloads (and Goose) are labeled before detection.
    agent_override = os.environ.get("MEMOR_HOOK_AGENT", "").strip().lower()
    if agent_override:
        request["_memor_agent"] = agent_override

    result = _send_to_sidecar(request)
    if result is None:
        if _start_sidecar():
            result = _send_to_sidecar(request)
    if result is None:
        result = _inline_fallback(request)

    from memor.hook_server import detect_agent, serialize_hook_stdout
    print(serialize_hook_stdout(detect_agent(request), result))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
