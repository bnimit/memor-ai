"""Tests for Memor proxy /health probe."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import urllib.error

from memor.proxy.health import check_proxy_health, wait_for_proxy_health


class _Resp:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_check_proxy_health_accepts_memor_json():
    body = json.dumps({"ok": True, "bind": "127.0.0.1"}).encode()
    with patch("urllib.request.urlopen", return_value=_Resp(200, body)):
        ok, detail = check_proxy_health(8421)
    assert ok is True
    assert detail == "ok"


def test_check_proxy_health_rejects_other_200():
    body = json.dumps({"status": "up"}).encode()
    with patch("urllib.request.urlopen", return_value=_Resp(200, body)):
        ok, detail = check_proxy_health(8421)
    assert ok is False
    assert "unexpected" in detail


def test_wait_for_proxy_health_times_out(monkeypatch):
    monkeypatch.setattr(
        "memor.proxy.health.check_proxy_health",
        lambda port, timeout=1.0: (False, "connection refused"),
    )
    monkeypatch.setattr("memor.proxy.health.time.sleep", lambda s: None)
    # Advance monotonic quickly
    t = {"v": 0.0}

    def mono():
        t["v"] += 1.0
        return t["v"]

    monkeypatch.setattr("memor.proxy.health.time.monotonic", mono)
    ok, detail = wait_for_proxy_health(8421, timeout=2.5, interval=0.5)
    assert ok is False
    assert "unhealthy" in detail
