"""Whole-store sweeps must run on an interval, not on every ingest.

Quality decay, cross-project promotion and near-duplicate compaction each scan
every memory. They were gated only on "did we ingest anything", which during an
active coding session is true on essentially every 30s poll, so the daemon
re-swept the whole store every 30 seconds and never went idle.
"""
from __future__ import annotations

from pathlib import Path

import memor.daemon as daemon


class _Unit:
    def __init__(self, key, mtime):
        self.state_key = key
        self.mtime = mtime
        self.path = None
        self.agent = "kimi"
        self.project = "p"


def _wire(monkeypatch, tmp_path, calls):
    """A cycle that always ingests something, with the sweeps counted."""
    monkeypatch.setattr(daemon, "MAINTENANCE_STAMP", tmp_path / "stamp")
    monkeypatch.setattr(daemon, "scan_all_sources",
                        lambda **kw: [_Unit("/x/a.jsonl", 1.0)])
    monkeypatch.setattr(daemon, "ingest_unit", lambda u, s, e: 1)
    monkeypatch.setattr(daemon, "distill_new_sessions",
                        lambda store, emb, llm, d: d)
    monkeypatch.setattr(daemon, "compact_memories",
                        lambda store, emb: calls.append("compact") or 0)
    monkeypatch.setattr(daemon, "auto_compact",
                        lambda store, emb: calls.append("auto") or None)

    import memor.global_memories as gm
    monkeypatch.setattr(gm, "run_promotion",
                        lambda store, emb, min_projects=3: calls.append("promote") or 0)


class _Store:
    def decay_quality(self, **kw):
        _Store.calls.append("decay")
        return 0


def test_sweeps_run_once_not_every_cycle(monkeypatch, tmp_path):
    calls: list[str] = []
    _Store.calls = calls
    _wire(monkeypatch, tmp_path, calls)

    state: dict[str, float] = {}
    for i in range(5):
        # A fresh mtime each cycle, so something is always newly ingested.
        def _scan(_i=i, **kw):
            return [_Unit("/x/a.jsonl", float(_i))]

        monkeypatch.setattr(daemon, "scan_all_sources", _scan)
        state, _d, _c = daemon.run_poll_cycle(
            state, _Store(), embedder=None, projects_dir=tmp_path)

    assert calls.count("promote") == 1, f"promotion ran {calls.count('promote')} times"
    assert calls.count("compact") == 1
    assert calls.count("decay") == 1


def test_sweeps_run_again_once_the_interval_passes(monkeypatch, tmp_path):
    calls: list[str] = []
    _Store.calls = calls
    _wire(monkeypatch, tmp_path, calls)
    monkeypatch.setattr(daemon, "MAINTENANCE_INTERVAL", 0)

    state: dict[str, float] = {}
    for i in range(3):
        def _scan(_i=i, **kw):
            return [_Unit("/x/a.jsonl", float(_i))]

        monkeypatch.setattr(daemon, "scan_all_sources", _scan)
        state, _d, _c = daemon.run_poll_cycle(
            state, _Store(), embedder=None, projects_dir=tmp_path)

    assert calls.count("promote") == 3


def test_stamp_survives_a_restart(tmp_path, monkeypatch):
    """A restart must not turn into a fresh full sweep."""
    monkeypatch.setattr(daemon, "MAINTENANCE_STAMP", tmp_path / "stamp")
    assert daemon._maintenance_due() is True
    daemon._mark_maintenance()
    assert daemon._maintenance_due() is False
    # Far enough in the future, it is due again.
    assert daemon._maintenance_due(now=10**12) is True
