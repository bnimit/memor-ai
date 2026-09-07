"""Randomized holdout, so a savings claim can be measured rather than estimated.

Everything else in the ledger is observational. Compression runs on every
eligible payload, so there is nothing to compare against: memor counts what it
removed and calls the difference a saving. That number is a tokenizer estimate
of a quantity nobody billed, and it cannot see the cost the compression itself
may have added -- a longer answer, a re-formed cache prefix.

A holdout supplies the missing arm. A fraction of eligible payloads are left
uncompressed at random, so the two groups differ only in the treatment, and the
provider's own billed tokens can be compared between them.

**Randomize per payload, not per session.** The obvious design is to hold out
whole sessions, which is what the one comparable product does. On this store
that fails: nine sessions with a coefficient of variation of 1.42 (mean 31,061
tokens, range 1,868 to 148,284) would need roughly 790 sessions per arm to
detect a 20% effect. Per-payload assignment compares like with like -- the same
kind of tool output, in the same conversation -- and the kept-ratio's CV of 0.38
brings that to about 57 requests per arm, which is a week of ordinary use rather
than a year.

The cost of measuring is real and bounded: the holdout fraction is the share of
savings deliberately given up to learn whether the rest are real.
"""
from __future__ import annotations

import hashlib
import os

#: Default share of eligible payloads left uncompressed. 10% buys a comparison
#: at the cost of a tenth of the savings, and reaches the ~57-per-arm floor in
#: a few hundred requests.
DEFAULT_HOLDOUT_FRACTION = 0.1

#: Ledger values for the two arms. ``None`` means the experiment was off, which
#: must stay distinguishable from "assigned to the compressed arm": rows from
#: before the holdout existed are not evidence about it.
ARM_TREATMENT = "compress"
ARM_CONTROL = "holdout"


def holdout_fraction() -> float:
    """Share of payloads to leave uncompressed, from the environment.

    Off by default. An experiment that runs without the operator knowing is
    one that quietly costs them tokens.
    """
    raw = os.environ.get("MEMOR_HOLDOUT_FRACTION", "").strip()
    if not raw:
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    if value <= 0:
        return 0.0
    return min(value, 1.0)


def is_enabled() -> bool:
    return holdout_fraction() > 0


def assign_arm(key: str, *, fraction: float | None = None) -> str:
    """Deterministically assign one payload to an arm.

    Hashing the payload rather than drawing a random number matters for a
    reason specific to this system: an agent resends its whole trajectory on
    every step, so the same tool output arrives many times. A fresh coin flip
    each time would compress a payload in one request and hold it out in the
    next, changing the cached prefix repeatedly and charging the experiment for
    cache writes that measure nothing. A hash keeps each payload in the arm it
    was first assigned to, for as long as it exists.
    """
    share = holdout_fraction() if fraction is None else fraction
    if share <= 0:
        return ARM_TREATMENT
    if share >= 1:
        return ARM_CONTROL
    digest = hashlib.sha256(key.encode("utf-8", "replace")).digest()
    # First four bytes as a fraction of the space; uniform enough for this.
    bucket = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return ARM_CONTROL if bucket < share else ARM_TREATMENT
