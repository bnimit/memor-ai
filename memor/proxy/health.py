"""Proxy liveness probe — Memor JSON /health, not bare TCP."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


def check_proxy_health(port: int, timeout: float = 1.0) -> tuple[bool, str]:
    """Single probe. Success only if HTTP 200 and JSON has ok=True."""
    url = f"http://127.0.0.1:{port}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            data = json.loads(body)
            if data.get("ok") is True:
                return True, "ok"
            return False, f"unexpected health body: {body[:120]}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e) or type(e).__name__


def wait_for_proxy_health(
    port: int,
    timeout: float = 45.0,
    interval: float = 0.5,
) -> tuple[bool, str]:
    """Poll until Memor proxy /health succeeds or timeout elapses."""
    deadline = time.monotonic() + timeout
    last = "not checked"
    while time.monotonic() < deadline:
        ok, last = check_proxy_health(port, timeout=min(interval, 1.0))
        if ok:
            return True, last
        time.sleep(interval)
    return False, f"proxy unhealthy after {timeout:.0f}s: {last}"
