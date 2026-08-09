"""Detecting a proxy that is serving code older than what is installed.

memor is commonly installed editable, so the files on disk move ahead of a
long-running proxy process every time the repo changes. Nothing surfaces that:
the version on disk reports the new number, the socket serves the old
behaviour, and a feature looks broken for a reason no log explains. This is
exactly what happened while building streaming usage capture -- the ledger kept
reporting cache writes as unmeasured because the running proxy predated the
code that records them.
"""
from __future__ import annotations

import json
import time

import pytest


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_health(monkeypatch, payload: dict | None, error: Exception | None = None):
    import urllib.request

    def fake_urlopen(url, timeout=None):
        if error is not None:
            raise error
        return _FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_no_warning_when_proxy_is_newer_than_the_sources(monkeypatch):
    from memor.cli import proxy_staleness_lines

    _patch_health(monkeypatch, {"ok": True, "started_at": time.time() + 3600})
    assert proxy_staleness_lines() == []


def test_warns_when_sources_changed_after_the_proxy_started(monkeypatch):
    from memor.cli import proxy_staleness_lines

    # Started long ago; every source file on disk is newer than that.
    _patch_health(monkeypatch, {"ok": True, "started_at": 1.0})
    lines = proxy_staleness_lines()
    assert lines, "a proxy older than its own sources must be reported"
    assert "older than what is installed" in lines[0]
    assert "memor service restart" in lines[1]


def test_warns_when_health_predates_restart_detection(monkeypatch):
    """A proxy too old to report started_at is itself the thing being detected."""
    from memor.cli import proxy_staleness_lines

    _patch_health(monkeypatch, {"ok": True, "mode": "compress"})
    lines = proxy_staleness_lines()
    assert lines
    assert "predates restart detection" in lines[0]


def test_silent_when_no_proxy_is_running(monkeypatch):
    """Not running the proxy is the default; it must not produce noise."""
    from memor.cli import proxy_staleness_lines

    _patch_health(monkeypatch, None, error=OSError("connection refused"))
    assert proxy_staleness_lines() == []


def test_silent_when_health_is_not_json(monkeypatch):
    import urllib.request

    from memor.cli import proxy_staleness_lines

    class _Garbage(_FakeResponse):
        def __init__(self):
            pass

        def read(self):
            return b"<html>not json</html>"

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=None: _Garbage())
    assert proxy_staleness_lines() == []


@pytest.mark.parametrize("seconds,expected", [
    (5, "5s ago"), (600, "10m ago"), (7200, "2h ago"), (300000, "3d ago"),
])
def test_ago_formats_readably(seconds, expected):
    from memor.cli import _ago

    assert _ago(time.time() - seconds) == expected


def test_health_endpoint_reports_build_identity():
    """The status check depends on these fields existing."""
    from fastapi.testclient import TestClient

    from memor.embed.fake import FakeEmbedder
    from memor.proxy.server import create_proxy_app

    import tempfile
    from pathlib import Path

    app = create_proxy_app(str(Path(tempfile.mkdtemp()) / "m.db"),
                           embedder=FakeEmbedder(dim=16))
    with TestClient(app) as client:
        health = client.get("/health").json()

    assert health["ok"] is True
    assert isinstance(health["started_at"], float)
    assert health["captures_stream_usage"] is True
    assert health["version"]


# --- restart safety ---------------------------------------------------------

def test_install_reports_a_healthy_proxy(monkeypatch):
    """Restarting must confirm the proxy came back, not assume it."""
    from memor import service

    _patch_health(monkeypatch, {"ok": True, "version": "9.9.9"})
    lines = service._verify_proxy_started(timeout=1.0)
    assert len(lines) == 1
    assert "healthy" in lines[0]
    assert "9.9.9" in lines[0]


def test_install_reports_a_proxy_that_never_came_back(monkeypatch):
    """The failure that matters: agents point at localhost, so a dead proxy
    takes them offline rather than degrading to a direct provider call."""
    from memor import service

    _patch_health(monkeypatch, None, error=OSError("connection refused"))
    lines = service._verify_proxy_started(timeout=0.5)
    assert any("ERROR" in line for line in lines)
    assert any("cannot reach their provider" in line for line in lines)
    # It must name the escape hatch, not just the problem.
    assert any("uninstall-proxy" in line for line in lines)


def test_install_treats_not_ok_health_as_failure(monkeypatch):
    from memor import service

    _patch_health(monkeypatch, {"ok": False, "error": "bad upstream"})
    lines = service._verify_proxy_started(timeout=0.5)
    assert any("ERROR" in line for line in lines)
