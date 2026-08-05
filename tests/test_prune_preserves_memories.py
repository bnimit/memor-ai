"""Prune must never retire a distilled memory because raw transcript matched it.

A ``memory`` is distilled *from* a ``session_chunk``, so the two routinely carry
identical text while being different objects: one is raw transcript, the other
is the curated artifact recall exists to serve. Collapsing them together
retired 2,442 of 2,457 distilled memories on a real store and silently
downgraded recall to ``extractive_only``.
"""
from __future__ import annotations

import time

from memor.prune import find_duplicates, prune


class _FakeRow(dict):
    def __getitem__(self, key):
        return super().__getitem__(key)


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        self._last = sql
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return {"c": len(self._rows)}

    def commit(self):
        pass


class _FakeStore:
    def __init__(self, rows):
        self.db = _FakeDB(rows)


def _row(aid, kind, text, created_at, project="p"):
    return _FakeRow(id=aid, kind=kind, project=project, text=text,
                    created_at=created_at)


def test_distilled_memory_survives_its_source_chunk():
    """The chunk is older, so a kind-blind pass retires the memory. It must not."""
    now = time.time()
    shared = "the same words in both records"
    rows = [
        _row("chunk-1", "session_chunk", shared, now - 100),
        _row("mem-1", "memory", shared, now),
    ]
    losers = find_duplicates(_FakeStore(rows))
    assert "mem-1" not in losers, "a distilled memory was retired for matching its source"


def test_duplicates_still_collapse_within_a_kind():
    """The real duplicate case must keep working: same kind, same text."""
    now = time.time()
    rows = [
        _row("chunk-1", "session_chunk", "identical text", now - 100),
        _row("chunk-2", "session_chunk", "identical text", now),
        _row("mem-1", "memory", "a distilled note", now - 50),
        _row("mem-2", "memory", "a distilled note", now),
    ]
    losers = find_duplicates(_FakeStore(rows))
    # The earliest of each kind survives; the later copy of each is retired.
    assert "chunk-2" in losers
    assert "mem-2" in losers
    assert "chunk-1" not in losers
    assert "mem-1" not in losers


def test_memories_in_different_projects_are_not_duplicates():
    now = time.time()
    rows = [
        _row("mem-a", "memory", "shared lesson", now - 10, project="alpha"),
        _row("mem-b", "memory", "shared lesson", now, project="beta"),
    ]
    assert find_duplicates(_FakeStore(rows)) == []
