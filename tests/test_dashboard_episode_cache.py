"""The dashboard must parse the transcript corpus once per page load, not once per endpoint.

Three endpoints need the same parse of thousands of transcripts and the page
requests them together. Each used to hold its own cache, so a cold dashboard
paid for the scan three times concurrently and every one of them was slow.
"""
from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from memor.dashboard import server as server_mod


def _count_scans(monkeypatch):
    """Replace the corpus scan with a counter, so we can see how often it runs.

    Both endpoints that consume it bail out early unless a boundary is
    configured, so the config is stubbed too -- otherwise the test passes
    without ever reaching the code it is meant to guard.
    """
    calls = []

    def fake_scan(projects_dir=None):
        calls.append(1)
        return []

    monkeypatch.setattr("memor.episodes.scan_episodes", fake_scan)
    monkeypatch.setattr("memor.recall_baseline.get_baseline", lambda: 1.0)
    monkeypatch.setattr("memor.config.load_config",
                        lambda: {"compress_started_at": 1.0})
    return calls


def test_endpoints_share_one_scan(tmp_path, monkeypatch):
    calls = _count_scans(monkeypatch)
    app = server_mod.create_app(str(tmp_path / "t.db"))
    client = TestClient(app)

    for endpoint in ("/api/recall-worth", "/api/recall-baseline", "/api/compression"):
        assert client.get(endpoint).status_code == 200

    # One parse serves all three, however many of them asked for it.
    assert len(calls) <= 1, f"corpus parsed {len(calls)} times for three endpoints"


def test_concurrent_callers_do_not_each_scan(tmp_path, monkeypatch):
    """A cold page load fires the endpoints at once; that must still be one scan."""
    calls = _count_scans(monkeypatch)
    app = server_mod.create_app(str(tmp_path / "t.db"))
    client = TestClient(app)

    endpoints = ["/api/recall-worth", "/api/recall-baseline", "/api/compression"]
    errors = []

    def hit(endpoint):
        try:
            assert client.get(endpoint).status_code == 200
        except Exception as exc:  # surface it on the main thread
            errors.append(exc)

    threads = [threading.Thread(target=hit, args=(e,)) for e in endpoints]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, errors
    assert len(calls) <= 1, f"corpus parsed {len(calls)} times under concurrency"
