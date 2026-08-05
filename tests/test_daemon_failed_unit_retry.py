"""A file that always fails to parse must not be retried on every poll.

The state key is what stops a unit being re-ingested. It used to be recorded
only on success, so a file that raised every time came back on every 30s poll
forever, and each retry re-ran the whole post-ingest pipeline behind
`new_ingested`. That is what kept the daemon at ~90% CPU.
"""
from __future__ import annotations

from pathlib import Path

import memor.daemon as daemon


class _Unit:
    def __init__(self, key, mtime):
        self.state_key = key
        self.mtime = mtime
        self.path = Path(key)
        self.agent = "kimi"
        self.project = "p"


def test_failing_unit_is_not_retried_forever(monkeypatch, tmp_path):
    attempts = []

    def boom(unit, store, embedder):
        attempts.append(unit.state_key)
        raise ValueError("unparseable")

    monkeypatch.setattr(daemon, "ingest_unit", boom)
    monkeypatch.setattr(daemon, "scan_all_sources",
                        lambda **kw: [_Unit("/x/wire.jsonl", 100.0)])

    state: dict[str, float] = {}
    for _ in range(3):
        state, _distilled, _counts = daemon.run_poll_cycle(
            state, store=None, embedder=None, projects_dir=tmp_path)

    assert len(attempts) == 1, f"failing unit was retried {len(attempts)} times"
    assert state["/x/wire.jsonl"] == 100.0


def test_a_changed_file_is_tried_again(monkeypatch, tmp_path):
    """Recording the failure must not blacklist the file permanently."""
    attempts = []

    def boom(unit, store, embedder):
        attempts.append(unit.mtime)
        raise ValueError("unparseable")

    monkeypatch.setattr(daemon, "ingest_unit", boom)

    state: dict[str, float] = {}
    for mtime in (100.0, 100.0, 200.0):
        def scan(_m=mtime, **kw):
            return [_Unit("/x/wire.jsonl", _m)]

        monkeypatch.setattr(daemon, "scan_all_sources", scan)
        state, _d, _c = daemon.run_poll_cycle(
            state, store=None, embedder=None, projects_dir=tmp_path)

    # Tried once at the first mtime, and again once the file actually changed.
    assert attempts == [100.0, 200.0]
