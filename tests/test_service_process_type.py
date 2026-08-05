"""User-facing services must not be filed under launchd's Background class.

ProcessType=Background caps CPU and forces throttled disk I/O. On the dashboard
that turned a 2.0s cold load of the transcript corpus into 9.2s -- same code,
same machine. The ingest daemon genuinely is background work and stays there.
"""
from __future__ import annotations

import memor.service as service


def _units(**kw):
    return {u["key"]: u for u in service._units("/usr/local/bin/memor", **kw)}


def test_dashboard_and_proxy_are_interactive():
    units = _units(with_dashboard=True, with_proxy=True)
    assert units["dashboard"].get("process_type") == "Interactive"
    assert units["proxy"].get("process_type") == "Interactive"


def test_ingest_daemon_stays_background():
    """The daemon really is background work; it should not preempt anything."""
    units = _units(with_dashboard=True, with_proxy=True)
    assert units["daemon"].get("process_type", "Background") == "Background"


def test_plist_carries_the_requested_process_type():
    plist = service._plist_content("x", ["/bin/memor", "dashboard"], "/tmp/x.log",
                                   process_type="Interactive")
    assert "<key>ProcessType</key>" in plist
    assert "<string>Interactive</string>" in plist
    assert "<string>Background</string>" not in plist


def test_plist_defaults_to_background():
    plist = service._plist_content("x", ["/bin/memor", "daemon"], "/tmp/x.log")
    assert "<string>Background</string>" in plist
