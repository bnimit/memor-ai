"""The quality term must stay inside its weight budget.

The retrieval score is a convex combination: relevance, recency and kind are
all normalized to [0, 1] and weighted to sum to 1.0. Quality was the one term
with no ceiling, and its source counters could exceed the recall count — so a
single artifact reached a quality of 157.7. At w_qual=0.10 that contributes
15.77 to its score, against the 0.80 a perfect, fresh, best-kind match can
reach. Ranking stopped being about relevance.

These tests pin the ceiling, the invariant that produced the overflow, and the
property that actually matters: a perfect match outranks a corrupt one.
"""
from __future__ import annotations

import pytest

from memor.retrieve.retriever import _bounded_quality
from memor.store.sqlite_store import (
    NEUTRAL_QUALITY,
    SqliteStore,
    clamp_quality,
    quality_from_counts,
)


@pytest.fixture
def store(tmp_path):
    return SqliteStore(str(tmp_path / "q.db"), dim=16)


# --- the ceiling --------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    (157.667, 1.0), (5.0, 1.0), (1.0, 1.0), (0.5, 0.5), (0.0, 0.0), (-3.0, 0.0),
])
def test_scores_are_clamped_into_the_unit_interval(raw, expected):
    assert clamp_quality(raw) == expected


def test_missing_score_is_the_neutral_prior():
    assert clamp_quality(None) == NEUTRAL_QUALITY
    assert _bounded_quality({}, "absent") == NEUTRAL_QUALITY


def test_unparseable_score_does_not_crash_ranking():
    assert _bounded_quality({"a": "corrupt"}, "a") == NEUTRAL_QUALITY


# --- the invariant that produced the overflow ---------------------------------


def test_use_count_above_recall_count_falls_back_to_the_prior():
    """The production case: 40 recalls, 2180 uses, 1770 rejections."""
    assert quality_from_counts(40, 2180, 1770) == NEUTRAL_QUALITY


def test_rejections_above_recall_count_fall_back_to_the_prior():
    assert quality_from_counts(5, 0, 99) == NEUTRAL_QUALITY


def test_never_recalled_is_the_prior_not_a_reward():
    assert quality_from_counts(0, 0, 0) == NEUTRAL_QUALITY


def test_sound_counts_still_score_normally():
    # Used every time it was recalled: high, but still bounded.
    assert 0.5 < quality_from_counts(10, 10, 0) <= 1.0
    # Never used: below the prior.
    assert quality_from_counts(10, 0, 0) < NEUTRAL_QUALITY
    # Rejected every time: lower still.
    assert quality_from_counts(10, 0, 10) < quality_from_counts(10, 0, 0)


@pytest.mark.parametrize("rc", [1, 3, 10, 100, 5000])
def test_no_sound_count_can_leave_the_unit_interval(rc):
    for uc in (0, rc // 2, rc):
        for nc in (0, rc // 2, rc):
            if uc + nc > rc * 2:
                continue
            assert 0.0 <= quality_from_counts(rc, uc, nc) <= 1.0


# --- the property that matters ------------------------------------------------


def test_a_perfect_match_outranks_a_corrupt_artifact():
    """Live weights: w_sim=0.50, w_rec=0.25, w_kind=0.15, w_qual=0.10."""
    def score(rel, recency, kind_boost, quality):
        return (0.50 * rel + 0.25 * recency + 0.15 * kind_boost
                + 0.10 * _bounded_quality({"x": quality}, "x"))

    perfect = score(1.0, 1.0, 0.3, NEUTRAL_QUALITY)
    corrupt = score(0.0, 0.0, 0.0, 157.667)
    assert corrupt < perfect, "an irrelevant artifact still outranks a perfect match"


def test_quality_cannot_exceed_its_weight_budget():
    """Whatever the stored value, the term contributes at most w_qual."""
    assert 0.10 * _bounded_quality({"x": 157.667}, "x") <= 0.10


# --- persistence --------------------------------------------------------------


def test_store_clamps_on_read(store):
    store.db.execute(
        "INSERT INTO memory_quality(artifact_id, recall_count, use_count, "
        "negative_count, quality_score) VALUES('a', 40, 2180, 1770, 157.667)")
    store.db.commit()
    assert store.get_quality_score("a") == 1.0
    assert store.get_quality_scores(["a"])["a"] == 1.0


def test_absent_artifact_reads_as_the_prior(store):
    assert store.get_quality_score("nope") == NEUTRAL_QUALITY


def test_recompute_uses_the_bounded_formula(store):
    store.record_recall(["a"])
    for _ in range(50):
        store.record_usage(["a"])
    assert 0.0 <= store.get_quality_score("a") <= 1.0


# --- the migration ------------------------------------------------------------


def test_reopening_repairs_out_of_range_rows(tmp_path):
    """An existing database must not need a manual fix."""
    path = str(tmp_path / "legacy.db")
    first = SqliteStore(path, dim=16)
    first.db.execute(
        "INSERT INTO memory_quality(artifact_id, recall_count, use_count, "
        "negative_count, quality_score) VALUES('a', 40, 2180, 1770, 157.667)")
    first.db.execute(
        "INSERT INTO memory_quality(artifact_id, recall_count, use_count, "
        "negative_count, quality_score) VALUES('b', 10, 4, 1, 0.417)")
    first.db.commit()
    first.db.close()

    reopened = SqliteStore(path, dim=16)
    row = reopened.db.execute(
        "SELECT quality_score FROM memory_quality WHERE artifact_id='a'").fetchone()
    assert row["quality_score"] == NEUTRAL_QUALITY, "corrupt counts must reset to the prior"

    untouched = reopened.db.execute(
        "SELECT quality_score FROM memory_quality WHERE artifact_id='b'").fetchone()
    assert untouched["quality_score"] == 0.417, "in-range rows must be left alone"
