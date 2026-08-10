"""Measure whether compressing older turns is worth it, net of cache.

`compress_older_turns` is off, so the pipeline sees 0.09% of the compressible
tool output in a request. Widening it is worth about 10% gross on the mass it
would expose, measured by replaying real payloads. But rewriting an older turn
rewrites the cached prefix, and Anthropic bills a cache write at 1.25x against
a cache read at 0.1x. With 165,974,863 cache-read tokens in the ledger, the
overhead can exceed the saving. Gross savings cannot answer this; only a
comparison of billed input units can.

So the flag is assigned per conversation, at random but stably, and both arms
are measured from the provider's own usage numbers. That matters more than it
sounds: the earlier recall panel compared arms that differed in project mix and
reported five sixths of a confound as an effect. Here the assignment is
randomised, so the arms differ only in the treatment.

Nothing in this module changes what is sent. It decides an arm and reports what
happened.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from memor.compression_worth import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
)

#: Share of conversations that get the wider compression window.
ENV_FRACTION = "MEMOR_OLDER_TURNS_EXPERIMENT"

#: Below this, a per-arm difference is noise. Cache behaviour is bursty --
#: one long conversation can dominate a day -- so this is deliberately higher
#: than the 25 used for a single-arm rate.
MIN_PER_ARM = 60


@dataclass
class ArmStats:
    """Billed input, in base-input-token equivalents, for one arm."""
    requests: int = 0
    upstream_input: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    tokens_before: int = 0
    tokens_after: int = 0

    @property
    def billed_units(self) -> float:
        return (
            self.upstream_input
            + self.cache_read * CACHE_READ_MULTIPLIER
            + self.cache_creation * CACHE_WRITE_MULTIPLIER
        )

    @property
    def billed_per_request(self) -> float:
        if self.requests <= 0:
            return 0.0
        return self.billed_units / self.requests

    @property
    def gross_saved(self) -> int:
        return self.tokens_before - self.tokens_after

    @property
    def cache_write_share(self) -> float:
        total = self.upstream_input + self.cache_read + self.cache_creation
        if total <= 0:
            return 0.0
        return self.cache_creation / total * 100


def experiment_fraction() -> float:
    """0.0 disables the experiment entirely, which is the default."""
    raw = os.environ.get(ENV_FRACTION, "0").strip()
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    return min(max(value, 0.0), 1.0)


def in_treatment(conversation_key: str, fraction: float | None = None) -> bool:
    """Stable per-conversation assignment.

    Hashed rather than random per request: a conversation that flipped arms
    mid-flight would rewrite its own cached prefix on the switch, charging the
    experiment for an effect the treatment does not have in steady state.
    """
    frac = experiment_fraction() if fraction is None else fraction
    if frac <= 0:
        return False
    if frac >= 1:
        return True
    digest = hashlib.sha256(f"older-turns:{conversation_key}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF < frac


def compare(rows: list[dict]) -> dict:
    """Compare billed input per request between the two arms.

    ``rows`` are savings-ledger rows carrying an ``arm`` of "treatment" or
    "control" and the provider's usage columns. Rows without usage are dropped:
    a row whose upstream numbers never arrived cannot say what it cost.
    """
    arms = {"treatment": ArmStats(), "control": ArmStats()}
    for row in rows:
        arm = arms.get(row.get("arm"))
        if arm is None:
            continue
        if row.get("upstream_input_tokens") is None:
            continue
        arm.requests += 1
        arm.upstream_input += int(row.get("upstream_input_tokens") or 0)
        arm.cache_read += int(row.get("upstream_cache_read_tokens") or 0)
        arm.cache_creation += int(row.get("upstream_cache_creation_tokens") or 0)
        arm.tokens_before += int(row.get("tokens_before") or 0)
        arm.tokens_after += int(row.get("tokens_after") or 0)

    treatment, control = arms["treatment"], arms["control"]
    scored = treatment.requests >= MIN_PER_ARM and control.requests >= MIN_PER_ARM

    result = {
        "scored": scored,
        "min_per_arm": MIN_PER_ARM,
        "treatment": _arm_dict(treatment),
        "control": _arm_dict(control),
    }
    if not scored:
        result["verdict"] = "insufficient_data"
        return result

    base = control.billed_per_request
    treat = treatment.billed_per_request
    delta = (base - treat) / base * 100 if base else 0.0
    result["net_delta_pct"] = round(delta, 2)

    # Gross savings are what the compressor believes it did; the delta is what
    # the provider actually billed. Reporting both makes a negative result
    # legible rather than surprising.
    result["gross_delta_pct"] = round(
        (treatment.gross_saved / treatment.tokens_before * 100)
        if treatment.tokens_before else 0.0, 2)

    if delta > 1.0:
        result["verdict"] = "saves"
    elif delta < -1.0:
        result["verdict"] = "costs"
    else:
        result["verdict"] = "no_effect"
    return result


def _arm_dict(arm: ArmStats) -> dict:
    return {
        "requests": arm.requests,
        "billed_units": round(arm.billed_units, 1),
        "billed_per_request": round(arm.billed_per_request, 1),
        "gross_saved": arm.gross_saved,
        "cache_write_share_pct": round(arm.cache_write_share, 2),
    }


def format_report(result: dict) -> str:
    t, c = result["treatment"], result["control"]
    lines = [
        "Older-turn compression, measured net of cache",
        f"  control   : {c['requests']:>5} requests, "
        f"{c['billed_per_request']:>10,.0f} billed units/request, "
        f"cache writes {c['cache_write_share_pct']:.2f}%",
        f"  treatment : {t['requests']:>5} requests, "
        f"{t['billed_per_request']:>10,.0f} billed units/request, "
        f"cache writes {t['cache_write_share_pct']:.2f}%",
    ]
    if not result["scored"]:
        lines.append(f"  verdict   : insufficient data "
                     f"(need {result['min_per_arm']} per arm)")
        return "\n".join(lines)
    lines += [
        f"  gross     : {result['gross_delta_pct']:+.2f}% "
        f"(what the compressor removed)",
        f"  net       : {result['net_delta_pct']:+.2f}% "
        f"(what the provider billed)",
        f"  verdict   : {result['verdict']}",
    ]
    if result["verdict"] == "costs":
        lines.append("  -> cache re-formation cost more than the tokens removed")
    return "\n".join(lines)
