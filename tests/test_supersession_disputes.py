"""Soft dispute store + detection."""
from __future__ import annotations

import time

import pytest

from memor.embed.fake import FakeEmbedder
from memor.retrieve.similarity import cosine_to_stored_sim
from memor.store.sqlite_store import SqliteStore
from memor.supersession import (
    find_and_record_disputes,
    validity_from_active_count,
)
from memor.types import Artifact


def _store(tmp_path):
    return SqliteStore(str(tmp_path / "m.db"), dim=16), FakeEmbedder(dim=16)


def _mem(store, e, text, *, mid, project="p", created=1000.0, mem_type="decision"):
    art = Artifact(
        id=mid, kind="memory", project=project, source="test",
        text=text, token_count=10, created_at=created,
        meta={"mem_type": mem_type},
    )
    store.add_artifacts([art], e.embed([text]))
    return art


def test_schema_has_disputes_and_validity(tmp_path):
    s, _ = _store(tmp_path)
    tables = {r[0] for r in s.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "disputes" in tables
    cols = [r[1] for r in s.db.execute("PRAGMA table_info(memory_quality)")]
    assert "validity" in cols


def test_add_dispute_and_recompute_validity(tmp_path):
    s, e = _store(tmp_path)
    _mem(s, e, "old redis cache", mid="o", created=1.0)
    _mem(s, e, "new postgres store", mid="m", created=2.0)
    assert s.add_dispute("o", "m")
    assert not s.add_dispute("o", "m")  # idempotent
    assert s.recompute_validity("o") == validity_from_active_count(1)
    assert s.get_validity("o") == pytest.approx(0.5)


def test_validity_floor_at_two_disputers(tmp_path):
    s, e = _store(tmp_path)
    _mem(s, e, "old", mid="o", created=1.0)
    _mem(s, e, "m1", mid="m1", created=2.0)
    _mem(s, e, "m2", mid="m2", created=3.0)
    s.add_dispute("o", "m1")
    s.add_dispute("o", "m2")
    assert s.recompute_validity("o") == pytest.approx(0.25)


def test_transitive_disputer_stops_counting(tmp_path):
    s, e = _store(tmp_path)
    _mem(s, e, "oldest", mid="o", created=1.0)
    _mem(s, e, "mid", mid="m", created=2.0)
    _mem(s, e, "newest", mid="n", created=3.0)
    s.add_dispute("o", "m")
    s.add_dispute("m", "n")  # m is disputed → does not count against o
    assert s.active_disputers("o") == []
    assert s.recompute_validity("o") == 1.0


def test_affirmations_dormant_recovers_validity(tmp_path):
    s, e = _store(tmp_path)
    _mem(s, e, "old", mid="o", created=1.0)
    _mem(s, e, "new", mid="m", created=2.0)
    s.add_dispute("o", "m")
    s.recompute_validity("o")
    s.affirm_dispute_on_use("o")
    assert s.get_validity("o") == pytest.approx(0.5)
    s.affirm_dispute_on_use("o")
    assert s.get_validity("o") == 1.0


def test_find_and_record_uses_mocked_knn_band(tmp_path, monkeypatch):
    s, e = _store(tmp_path)
    old = _mem(s, e, "we use redis for cache", mid="o", created=1.0)
    new = _mem(s, e, "we switched to postgres for cache", mid="m", created=2.0)
    band_sim = cosine_to_stored_sim(0.85)

    def fake_search(vec, scope, k=8):
        return [(old, band_sim)]

    monkeypatch.setattr(s, "search", fake_search)
    disputed = find_and_record_disputes(s, e, "m", project="p")
    assert disputed == ["o"]
    assert s.db.execute(
        "SELECT active FROM artifacts WHERE id='o'").fetchone()["active"] == 1
    assert s.get_validity("o") == pytest.approx(0.5)


def test_find_skips_when_newer_than_candidate(tmp_path, monkeypatch):
    s, e = _store(tmp_path)
    newer = _mem(s, e, "newer fact", mid="newer", created=5.0)
    older_m = _mem(s, e, "older disputer attempt", mid="m", created=1.0)
    band_sim = cosine_to_stored_sim(0.85)

    monkeypatch.setattr(s, "search", lambda *a, **k: [(newer, band_sim)])
    assert find_and_record_disputes(s, e, "m", project="p") == []
