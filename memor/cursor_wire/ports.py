"""Wire listen port selection and TCP health probes."""
from __future__ import annotations

import os
import socket
import time


DEFAULT_WIRE_PORT = 8080
WIRE_PORT_RANGE = range(8080, 8091)


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex((host, port)) == 0


def check_wire_health(port: int, timeout: float = 1.0) -> tuple[bool, str]:
    """TCP connect to mitmdump listen port (no Memor JSON /health)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True, "tcp ok"
            return False, "connection refused"
    except Exception as exc:
        return False, str(exc) or type(exc).__name__


def wait_for_wire_health(
    port: int,
    timeout: float = 15.0,
    interval: float = 0.4,
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    last = "not checked"
    while time.monotonic() < deadline:
        ok, last = check_wire_health(port, timeout=min(interval, 1.0))
        if ok:
            return True, last
        time.sleep(interval)
    return False, f"wire unhealthy after {timeout:.0f}s: {last}"


def resolve_wire_port(
    *,
    preferred: int | None = None,
    allow_env: bool = True,
) -> tuple[int, list[str]]:
    """Pick a free wire listen port. Returns (port, human notes)."""
    notes: list[str] = []
    env_raw = os.environ.get("MEMOR_CURSOR_WIRE_PORT") if allow_env else None
    if env_raw is not None and str(env_raw).strip():
        port = int(env_raw)
        if port_in_use(port):
            raise RuntimeError(
                f"MEMOR_CURSOR_WIRE_PORT={port} is already in use. "
                "Free it or choose another port."
            )
        return port, notes

    if preferred is not None:
        if not port_in_use(preferred):
            return preferred, notes
        notes.append(
            f"Configured wire port {preferred} is busy; scanning {WIRE_PORT_RANGE.start}–{WIRE_PORT_RANGE.stop - 1}."
        )

    for port in WIRE_PORT_RANGE:
        if not port_in_use(port):
            if port != DEFAULT_WIRE_PORT:
                notes.append(
                    f"Port {DEFAULT_WIRE_PORT} in use; using {port}. Cursor settings will match."
                )
            return port, notes

    raise RuntimeError(
        f"No free wire port in {WIRE_PORT_RANGE.start}–{WIRE_PORT_RANGE.stop - 1}. "
        "Free a port or set MEMOR_CURSOR_WIRE_PORT."
    )
