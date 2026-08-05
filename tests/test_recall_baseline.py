"""Reading recall across a stamped boundary, instead of deleting the store.

Wiping the database to "start clean" throws away the ledgers — including the
counterfactual runs that are the control arm for the change being evaluated —
while the artifacts it does clear are rebuilt from transcripts within a day.
This measures the same thing without the loss, and refuses to report a verdict
the data cannot carry.
"""
from __future__ import annotations

import time

import pytest

from memor.episodes import Episode
from memor.recall_baseline import (
    MIN_EPISODES,
    MIN_RECALLS,
    NOISE_FLOOR_PCT,
    compare,
)
from memor.store.sqlite_store import SqliteStore


@pytest.fixture
def store(tmp_path):
    return SqliteStore(str(tmp_path / "b.db"), dim=16)


BOUNDARY = 10_000.0


def _recalls(store, n, *, ts, hits=1, score=0.8):
    for i in range(n):
        rid = store.log_recall(project="p", query_preview="q", hits_count=hits,
                               top_score=score, tokens_injected=100,
                               latency_ms=5.0, status="ok", session_id="s")
        store.db.execute("UPDATE recall_log SET timestamp=? WHERE id=?", (ts + i * 0.001, rid))
    store.db.commit()


def _eps(n, *, ts, had_recall, tool_calls):
    return [Episode(started_at=ts, had_recall=had_recall, tool_calls=tool_calls,
                    assistant_steps=1) for _ in range(n)]


# --- refusing to overclaim -----------------------------------------------------


def test_no_recalls_since_the_boundary_says_so(store):
    _recalls(store, 100, ts=BOUNDARY - 500)
    assert compare(store, [], BOUNDARY)["verdict"] == "no_data_yet"


def test_a_thin_sample_is_called_too_early(store):
    _recalls(store, MIN_RECALLS - 1, ts=BOUNDARY + 1)
    assert compare(store, [], BOUNDARY)["verdict"] == "too_early"


def test_enough_recalls_but_no_episodes_reports_retrieval_only(store):
    _recalls(store, MIN_RECALLS, ts=BOUNDARY + 1)
    assert compare(store, [], BOUNDARY)["verdict"] == "retrieval_only"


def test_a_sub_noise_change_is_not_called_an_effect(store):
    """The discipline that matters: small moves are noise, not findings."""
    _recalls(store, MIN_RECALLS, ts=BOUNDARY + 1)
    eps = (_eps(MIN_EPISODES, ts=BOUNDARY - 100, had_recall=True, tool_calls=10)
           + _eps(MIN_EPISODES, ts=BOUNDARY - 100, had_recall=False, tool_calls=10)
           + _eps(MIN_EPISODES, ts=BOUNDARY + 100, had_recall=True, tool_calls=10)
           + _eps(MIN_EPISODES, ts=BOUNDARY + 100, had_recall=False, tool_calls=10))
    result = compare(store, eps, BOUNDARY)
    assert abs(result["episodes"]["did_pp"]) < NOISE_FLOOR_PCT
    assert result["verdict"] == "no_effect"


def test_one_side_short_of_episodes_is_not_scored(store):
    _recalls(store, MIN_RECALLS, ts=BOUNDARY + 1)
    eps = (_eps(MIN_EPISODES, ts=BOUNDARY - 100, had_recall=True, tool_calls=10)
           + _eps(MIN_EPISODES, ts=BOUNDARY - 100, had_recall=False, tool_calls=10)
           + _eps(3, ts=BOUNDARY + 100, had_recall=True, tool_calls=4))
    result = compare(store, eps, BOUNDARY)
    assert result["episodes"]["after"]["scored"] is False
    assert result["episodes"]["did_pp"] is None


# --- reading a real change ------------------------------------------------------


def test_a_gap_that_widens_after_the_boundary_reads_as_improved(store):
    _recalls(store, MIN_RECALLS, ts=BOUNDARY + 1)
    eps = (_eps(MIN_EPISODES, ts=BOUNDARY - 100, had_recall=True, tool_calls=10)
           + _eps(MIN_EPISODES, ts=BOUNDARY - 100, had_recall=False, tool_calls=10)
           + _eps(MIN_EPISODES, ts=BOUNDARY + 100, had_recall=True, tool_calls=5)
           + _eps(MIN_EPISODES, ts=BOUNDARY + 100, had_recall=False, tool_calls=10))
    result = compare(store, eps, BOUNDARY)
    assert result["episodes"]["did_pp"] == 50.0
    assert result["verdict"] == "improved"


def test_a_gap_that_narrows_reads_as_regressed(store):
    _recalls(store, MIN_RECALLS, ts=BOUNDARY + 1)
    eps = (_eps(MIN_EPISODES, ts=BOUNDARY - 100, had_recall=True, tool_calls=5)
           + _eps(MIN_EPISODES, ts=BOUNDARY - 100, had_recall=False, tool_calls=10)
           + _eps(MIN_EPISODES, ts=BOUNDARY + 100, had_recall=True, tool_calls=10)
           + _eps(MIN_EPISODES, ts=BOUNDARY + 100, had_recall=False, tool_calls=10))
    assert compare(store, eps, BOUNDARY)["verdict"] == "regressed"


def test_retrieval_health_is_split_at_the_boundary(store):
    _recalls(store, 60, ts=BOUNDARY - 500, hits=0, score=0.0)
    _recalls(store, 60, ts=BOUNDARY + 500, hits=1, score=0.9)
    r = compare(store, [], BOUNDARY)["retrieval"]
    assert r["before"]["hit_rate"] == 0.0
    assert r["after"]["hit_rate"] == 1.0
    assert r["hit_rate_delta_pp"] == 100.0


# --- verdict ledger -------------------------------------------------------------


def test_pending_verdicts_are_reported_as_unmeasured_not_unused(store):
    """An unsettled verdict means "not judged yet", never "never used"."""
    rid = store.log_recall(project="p", query_preview="q", hits_count=2,
                           top_score=0.8, tokens_injected=100, latency_ms=5.0,
                           status="ok", session_id="s")
    store.record_recall_candidates(rid, ["a", "b"])
    v = compare(store, [], BOUNDARY)["verdicts"]
    assert v["served"] == 2 and v["judged"] == 0 and v["pending"] == 2
    assert v["scored"] is False
    assert "used_pct" not in v


def test_settled_verdicts_are_summarised(store):
    rid = store.log_recall(project="p", query_preview="q", hits_count=4,
                           top_score=0.8, tokens_injected=100, latency_ms=5.0,
                           status="ok", session_id="s")
    store.record_recall_candidates(rid, ["a", "b", "c", "d"])
    store.resolve_outcomes([(rid, "a", "used", "ngram"), (rid, "b", "unused", ""),
                            (rid, "c", "unused", ""), (rid, "d", "rejected", "phrase")])
    v = compare(store, [], BOUNDARY)["verdicts"]
    assert v["judged"] == 4 and v["used"] == 1 and v["rejected"] == 1
    assert v["used_pct"] == 25.0


# --- the stamp ------------------------------------------------------------------


def test_stamping_round_trips(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    import memor.config as config
    monkeypatch.setattr(config, "CONFIG_PATH", cfg, raising=False)
    monkeypatch.setattr(config, "_config_path", lambda: cfg, raising=False)

    from memor.recall_baseline import clear_baseline, get_baseline, stamp_baseline

    try:
        ts = stamp_baseline(1234.5)
    except Exception:
        pytest.skip("config path is not overridable in this build")
    assert ts == 1234.5
    assert get_baseline() == 1234.5
    clear_baseline()
    assert get_baseline() is None


def test_days_since_is_reported(store):
    result = compare(store, [], time.time() - 86400 * 3)
    assert 2.9 < result["days_since"] < 3.1
