"""Is each wired agent still reading memory, or did it quietly stop?

Every other number memor reports answers "how well is this working". None of
them answer "is this still running at all", and those fail differently. A
retrieval score degrades visibly; a dead integration reports nothing, which is
indistinguishable from a quiet week.

That gap has already cost real evidence. Cursor last recalled on 2026-08-11 and
jcode on 2026-08-22, and neither stopping surfaced anywhere. Worse, an
adversarial review of this project read a lifetime aggregate -- "Codex: 38
recalls, 0 hits" -- and concluded the Codex integration was broken. Those 38
rows were from June, before the project-resolution bug behind them was fixed in
44ed7fe on 2026-08-05. The metric was accurate and the conclusion was false,
because a lifetime total silently mixes evidence from before and after a fix.

So this module reports two things a total cannot:

* **staleness** -- when each agent last read, and whether that is recent enough
  to believe the integration still works.
* **freshness of the evidence** -- how much of a per-agent statistic was
  generated after the code that produced it last changed, so a fixed bug stops
  being quoted as a current one.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from memor.store.sqlite_store import SqliteStore

#: An agent read this recently: the integration is working.
FRESH_DAYS = 7

#: Nothing since this long ago. Not proof of breakage -- a tool you did not use
#: is silent for the same reason a broken one is -- but the point at which
#: "probably fine" stops being the right default.
STALE_DAYS = 21

#: Recalls below this are too few to draw any conclusion from, in either
#: direction. The Codex misreading came from treating 38 rows as a verdict.
MIN_SAMPLE = 20

_DAY = 86_400

#: Commits that changed how a recall resolves its project or gets logged.
#: Statistics from before the relevant one describe code that no longer exists.
#: Dates, not hashes, because the store records time and not revisions. The
#: epoch is derived rather than written out: a hand-computed constant here was
#: two days off, which would have silently mis-split every agent's history.
_BEHAVIOUR_CHANGE_DATES: tuple[tuple[str, str], ...] = (
    # 44ed7fe "Give the proxy the project, and make it log every recall".
    # Before this the proxy could not resolve a project and every proxied
    # recall searched "unknown", a bucket with no artifacts in it.
    ("proxy project resolution", "2026-08-05"),
)

BEHAVIOUR_CHANGES: tuple[tuple[str, float, str], ...] = tuple(
    (
        label,
        datetime.strptime(day, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp(),
        day,
    )
    for label, day in _BEHAVIOUR_CHANGE_DATES
)


@dataclass
class AgentLiveness:
    """One agent's reading activity, and how much of it is still current."""

    agent: str
    recalls: int = 0
    hits: int = 0
    last_recall_at: float = 0.0
    recent_recalls: int = 0
    recent_hits: int = 0
    stale_recalls: int = 0
    status: str = "never"
    notes: list[str] = field(default_factory=list)

    @property
    def days_since_recall(self) -> float | None:
        if not self.last_recall_at:
            return None
        return max(0.0, (time.time() - self.last_recall_at) / _DAY)

    @property
    def hit_rate(self) -> float | None:
        """Hit rate over recent recalls only, or None when too few to judge."""
        if self.recent_recalls < MIN_SAMPLE:
            return None
        return self.recent_hits / self.recent_recalls


def _status_for(days: float | None, recalls: int) -> str:
    if not recalls or days is None:
        return "never"
    if days <= FRESH_DAYS:
        return "live"
    if days <= STALE_DAYS:
        return "quiet"
    return "stale"


def agent_liveness(
    store: SqliteStore, *, now: float | None = None
) -> list[AgentLiveness]:
    """Per-agent reading activity, newest reader first.

    Splits each agent's history at the last behaviour change, because a total
    that spans a fix reports the bug as though it were still present.
    """
    now = time.time() if now is None else now
    cutoff = max((ts for _, ts, _ in BEHAVIOUR_CHANGES), default=0.0)

    rows = store.db.execute(
        """
        SELECT agent,
               COUNT(*)                                   AS recalls,
               SUM(CASE WHEN hits_count > 0 THEN 1 ELSE 0 END) AS hits,
               MAX(timestamp)                             AS last_at,
               SUM(CASE WHEN timestamp >= ? THEN 1 ELSE 0 END) AS recent,
               SUM(CASE WHEN timestamp >= ? AND hits_count > 0
                        THEN 1 ELSE 0 END)                AS recent_hits
        FROM recall_log
        GROUP BY agent
        """,
        (cutoff, cutoff),
    ).fetchall()

    results: list[AgentLiveness] = []
    for row in rows:
        agent = row["agent"] or "unknown"
        recalls = row["recalls"] or 0
        recent = row["recent"] or 0
        entry = AgentLiveness(
            agent=agent,
            recalls=recalls,
            hits=row["hits"] or 0,
            last_recall_at=row["last_at"] or 0.0,
            recent_recalls=recent,
            recent_hits=row["recent_hits"] or 0,
            stale_recalls=recalls - recent,
        )
        days = (
            None if not entry.last_recall_at
            else max(0.0, (now - entry.last_recall_at) / _DAY)
        )
        entry.status = _status_for(days, recalls)
        entry.notes = _notes_for(entry)
        results.append(entry)

    results.sort(key=lambda e: -e.last_recall_at)
    return results


def _notes_for(entry: AgentLiveness) -> list[str]:
    """Say what the numbers cannot say for themselves."""
    notes: list[str] = []

    if entry.stale_recalls and not entry.recent_recalls:
        label = BEHAVIOUR_CHANGES[-1][2] if BEHAVIOUR_CHANGES else "a fix"
        notes.append(
            f"all {entry.stale_recalls} recalls predate the {label} fix; "
            "they describe code that no longer runs"
        )
    elif entry.stale_recalls:
        notes.append(
            f"{entry.stale_recalls} of {entry.recalls} recalls predate the "
            "last behaviour change and are excluded from the hit rate"
        )

    if entry.recent_recalls and entry.recent_recalls < MIN_SAMPLE:
        notes.append(
            f"only {entry.recent_recalls} current recalls, too few to judge"
        )

    if entry.status == "stale":
        notes.append("stopped reading; check the integration is still wired")

    return notes


def liveness_summary(
    store: SqliteStore, *, now: float | None = None
) -> dict:
    """Rollup for the dashboard and ``memor doctor``."""
    agents = agent_liveness(store, now=now)
    return {
        "agents": [
            {
                "agent": a.agent,
                "status": a.status,
                "recalls": a.recalls,
                "recent_recalls": a.recent_recalls,
                "hit_rate": a.hit_rate,
                "days_since_recall": a.days_since_recall,
                "notes": a.notes,
            }
            for a in agents
        ],
        "live": sum(1 for a in agents if a.status == "live"),
        "stale": sum(1 for a in agents if a.status == "stale"),
        "judgeable": sum(1 for a in agents if a.hit_rate is not None),
    }
