"""True-cosine conversion for dispute bands (store uses 1−L2)."""
from __future__ import annotations

import math

import pytest

from memor.retrieve.similarity import (
    DISPUTE_COSINE_HI,
    DISPUTE_COSINE_LO,
    cosine_in_dispute_band,
    cosine_to_stored_sim,
    stored_sim_to_cosine,
)


def test_identical_vectors_are_cosine_one():
    assert stored_sim_to_cosine(1.0) == 1.0


def test_orthogonal_unit_vectors():
    # L2(orthogonal unit) = √2 → stored_sim = 1 − √2
    sim = 1.0 - math.sqrt(2.0)
    assert stored_sim_to_cosine(sim) == pytest.approx(0.0, abs=1e-9)


def test_round_trip_band_edges():
    for cos in (DISPUTE_COSINE_LO, 0.85, DISPUTE_COSINE_HI - 1e-9):
        sim = cosine_to_stored_sim(cos)
        assert stored_sim_to_cosine(sim) == pytest.approx(cos, abs=1e-9)


def test_dispute_band_includes_lo_excludes_hi():
    assert cosine_in_dispute_band(cosine_to_stored_sim(0.80))
    assert cosine_in_dispute_band(cosine_to_stored_sim(0.85))
    assert not cosine_in_dispute_band(cosine_to_stored_sim(0.92))
    assert not cosine_in_dispute_band(cosine_to_stored_sim(0.79))
    assert not cosine_in_dispute_band(cosine_to_stored_sim(0.95))
