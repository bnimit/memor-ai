"""Store search returns 1−L2, not cosine. Dispute bands need true cosine."""
from __future__ import annotations

import math

#: Inclusive lower bound (true cosine) for soft disputes.
DISPUTE_COSINE_LO = 0.80
#: Exclusive upper bound — at/above this is exact dedup, not a dispute.
DISPUTE_COSINE_HI = 0.92


def stored_sim_to_cosine(stored_sim: float) -> float:
    """Convert sqlite-vec default L2 distance expressed as ``1 - L2`` to cosine.

    For unit vectors, L2 = √(2 − 2 cos θ), so
    ``stored_sim = 1 − L2`` ⇒ ``cos = 1 − (1 − stored_sim)² / 2``.
    """
    l2 = 1.0 - float(stored_sim)
    return 1.0 - (l2 * l2) / 2.0


def cosine_to_stored_sim(cosine: float) -> float:
    """Inverse of :func:`stored_sim_to_cosine` for fixtures and band edges."""
    cos = max(-1.0, min(1.0, float(cosine)))
    l2 = math.sqrt(max(0.0, 2.0 - 2.0 * cos))
    return 1.0 - l2


def cosine_in_dispute_band(stored_sim: float) -> bool:
    """True when store ``sim`` maps into [DISPUTE_COSINE_LO, DISPUTE_COSINE_HI)."""
    cos = stored_sim_to_cosine(stored_sim)
    return DISPUTE_COSINE_LO <= cos < DISPUTE_COSINE_HI
