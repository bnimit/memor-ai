"""Compare recall before and after a stamped change, without deleting anything.

The tempting move after fixing retrieval is to wipe the store and start clean.
It costs more than it looks. Artifacts are derived — the daemon rebuilds them
from the same transcripts — but the ledgers are primary data with no other
copy, and among them are the counterfactual runs that are the *control arm* for
the very fixes you want to evaluate. Deleting the database deletes the
comparison, not the clutter.

What is actually wanted is a clean measurement boundary, which is the pattern
already used for compression: stamp an instant, then read across it. Everything
before becomes the control rather than noise.

Three readings, weakest to strongest:

* **Retrieval health** — hit rate, score, tokens per recall. Cheap, immediate,
  and only says whether retrieval found things, not whether they helped.
* **Verdicts** — the share of served memories the agent actually used, from the
  outcome ledger. Only exists after the boundary, because nothing recorded it
  before.
* **Difference-in-differences** — the with-recall/without-recall gap on each
  side of the boundary, compared. Slower to reach significance, but the only
  one that controls for the work simply changing over time.
"""
from __future__ import annotations

import time
from statistics import median

BASELINE_KEY = "recall_baseline_at"

#: Recalls needed on each side before a rate is reported as a rate.
MIN_RECALLS = 50

#: Episodes needed per arm before the difference-in-differences is scored.
MIN_EPISODES = 30

#: Below this, a difference is indistinguishable from judge and sampling noise
#: on this corpus. Measured, not chosen: the counterfactual eval swings this
#: much between identical runs at n~77.
NOISE_FLOOR_PCT = 6.0


def stamp_baseline(when: float | None = None) -> float:
    """Record the instant a recall change went live."""
    from memor.config import load_config, save_config

    cfg = load_config()
    ts = float(when) if when else time.time()
    cfg[BASELINE_KEY] = ts
    save_config(cfg)
    return ts


def get_baseline() -> float | None:
    from memor.config import load_config

    value = load_config().get(BASELINE_KEY)
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def clear_baseline() -> None:
    from memor.config import load_config, save_config

    cfg = load_config()
    cfg.pop(BASELINE_KEY, None)
    save_config(cfg)


def _retrieval_side(store, *, since: float | None, until: float | None) -> dict:
    clauses, params = [], []
    if since is not None:
        clauses.append("timestamp >= ?")
        params.append(since)
    if until is not None:
        clauses.append("timestamp < ?")
        params.append(until)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = store.db.execute(
        f"SELECT hits_count, top_score, tokens_injected FROM recall_log{where}",
        params).fetchall()
    if not rows:
        return {"recalls": 0, "scored": False}
    hits = [r for r in rows if (r["hits_count"] or 0) > 0]
    return {
        "recalls": len(rows),
        "hit_rate": round(len(hits) / len(rows), 3),
        "median_top_score": round(median(r["top_score"] or 0.0 for r in hits), 3) if hits else 0.0,
        "median_tokens": round(median(r["tokens_injected"] or 0 for r in hits), 1) if hits else 0.0,
        "scored": len(rows) >= MIN_RECALLS,
    }


def _verdicts(store) -> dict:
    """What share of served memories the agent actually used.

    Only meaningful after the boundary: before it, nothing recorded a verdict,
    so an empty result here means "not measured yet", never "never used".
    """
    try:
        row = store.db.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(outcome != 'pending') AS judged,"
            " SUM(outcome = 'used') AS used,"
            " SUM(outcome = 'rejected') AS rejected"
            " FROM recall_outcomes").fetchone()
    except Exception:
        return {"served": 0, "judged": 0, "scored": False}
    total = row["total"] or 0
    judged = row["judged"] or 0
    used = row["used"] or 0
    rejected = row["rejected"] or 0
    out = {
        "served": total,
        "judged": judged,
        "pending": total - judged,
        "used": used,
        "rejected": rejected,
        "scored": judged > 0,
    }
    if judged:
        out["used_pct"] = round(used / judged * 100, 1)
        out["rejected_pct"] = round(rejected / judged * 100, 1)
    return out


def _arm_gap(episodes: list) -> dict:
    """Median tool calls with recall versus without, for one side."""
    usable = [e for e in episodes if e.assistant_steps > 0]
    with_r = [e for e in usable if e.had_recall]
    without = [e for e in usable if not e.had_recall]
    cell = {"n_with": len(with_r), "n_without": len(without), "gap_pct": None,
            "scored": len(with_r) >= MIN_EPISODES and len(without) >= MIN_EPISODES}
    if not cell["scored"]:
        return cell
    mw = median(e.tool_calls for e in with_r)
    mo = median(e.tool_calls for e in without)
    cell["median_with"] = mw
    cell["median_without"] = mo
    if mo:
        cell["gap_pct"] = round((mo - mw) / mo * 100, 1)
    return cell


def compare(store, episodes: list, boundary: float) -> dict:
    """Read recall across the boundary. Never claims more than the data holds."""
    before_eps = [e for e in episodes if e.started_at and e.started_at < boundary]
    after_eps = [e for e in episodes if e.started_at and e.started_at >= boundary]

    retrieval = {
        "before": _retrieval_side(store, since=None, until=boundary),
        "after": _retrieval_side(store, since=boundary, until=None),
    }
    if retrieval["before"].get("scored") and retrieval["after"].get("scored"):
        b, a = retrieval["before"], retrieval["after"]
        retrieval["hit_rate_delta_pp"] = round((a["hit_rate"] - b["hit_rate"]) * 100, 1)

    episodes_cmp = {"before": _arm_gap(before_eps), "after": _arm_gap(after_eps)}
    did = None
    if episodes_cmp["before"]["scored"] and episodes_cmp["after"]["scored"]:
        gb, ga = episodes_cmp["before"]["gap_pct"], episodes_cmp["after"]["gap_pct"]
        if gb is not None and ga is not None:
            did = round(ga - gb, 1)
    episodes_cmp["did_pp"] = did

    result = {
        "boundary": boundary,
        "days_since": round((time.time() - boundary) / 86400, 1),
        "retrieval": retrieval,
        "verdicts": _verdicts(store),
        "episodes": episodes_cmp,
    }
    result["verdict"] = _verdict(result)
    return result


def _verdict(result: dict) -> str:
    """Refuse to call a result the data cannot carry."""
    after = result["retrieval"]["after"]
    if not after.get("recalls"):
        return "no_data_yet"
    if not after.get("scored"):
        return "too_early"
    did = result["episodes"].get("did_pp")
    if did is None:
        return "retrieval_only"
    if abs(did) < NOISE_FLOOR_PCT:
        return "no_effect"
    return "improved" if did > 0 else "regressed"


VERDICT_TEXT = {
    "no_data_yet": "No recalls since the baseline was stamped.",
    "too_early": f"Fewer than {MIN_RECALLS} recalls since the baseline — too early to read.",
    "retrieval_only": (
        "Retrieval health is comparable; not enough episodes on both sides yet "
        "to say whether the work itself got cheaper."
    ),
    "no_effect": (
        f"The with/without gap moved by less than {NOISE_FLOOR_PCT}pp across the "
        "boundary, which this corpus cannot distinguish from noise."
    ),
    "improved": "Recall closed more of the gap after the change than before it.",
    "regressed": "Recall closed less of the gap after the change than before it.",
}
