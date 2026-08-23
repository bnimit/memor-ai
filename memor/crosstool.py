"""Did a memory written in one tool help in another?

That question is the reason memor exists, and until now nothing answered it.
The store has always been shared -- scoped by project, never by agent -- so
knowledge does cross. What was missing was any way to see whether it *helped*
once it crossed.

The distinction this module draws is between a memory's **writer** (the harness
whose session produced it) and its **reader** (the agent that was later served
it). When they differ, the recall is cross-tool.

One subtlety decides the number. ``distill`` and ``promotion`` are memor's own
processing stages, not rival harnesses: a distilled insight belongs to whichever
tool later reads it. Counting them as foreign writers inflated the cross-tool
figure from 37 to 44 on the local store, which is a 19% overstatement of the
product's headline claim.
"""
from __future__ import annotations

from memor.store.sqlite_store import SqliteStore

#: Sources that are memor's own pipeline rather than a coding agent. A memory
#: these produced is nobody's and everybody's, so reading one is never
#: cross-tool.
INTERNAL_SOURCES = frozenset({"distill", "promotion"})

#: The harness whose transcripts each reading agent produces. Cursor has no
#: ingest source of its own -- it reads Claude Code's store -- so a Cursor
#: recall of a ``claude_code`` artifact is the same tool by a different name.
READER_TO_WRITER = {
    "claude": "claude_code",
    "cursor": "claude_code",
    "jcode": "jcode",
    "goose": "goose",
    "kimi": "kimi",
    "codex": "codex",
}

#: Verdicts that represent a settled judgement. ``pending`` means the loop never
#: reached that recall, which is an instrumentation gap rather than a negative
#: result, so it is reported separately instead of counted as failure.
JUDGED = ("used", "rejected")


def is_cross_tool(reader_agent: str, writer_source: str) -> bool:
    """Whether a recall carried knowledge between two different harnesses."""
    if not reader_agent or not writer_source:
        return False
    if writer_source in INTERNAL_SOURCES:
        return False
    return READER_TO_WRITER.get(reader_agent, reader_agent) != writer_source


def cross_tool_stats(store: SqliteStore) -> dict:
    """Cross-tool versus same-tool outcomes, and the reader/writer breakdown.

    ``use_rate`` is over judged recalls only. Including ``pending`` would make
    an unmeasured pair look like a failed one, which is the precise confusion
    this module was built to end.
    """
    rows = store.db.execute(
        """
        SELECT rl.agent AS reader, a.source AS writer,
               ro.outcome AS outcome, COUNT(*) AS n
        FROM recall_outcomes ro
        JOIN recall_log rl ON rl.id = ro.recall_id
        JOIN artifacts a   ON a.id  = ro.artifact_id
        GROUP BY reader, writer, outcome
        """
    ).fetchall()

    buckets = {"cross_tool": _empty(), "same_tool": _empty()}
    pairs: dict[tuple[str, str], dict] = {}

    for row in rows:
        reader = row["reader"] or "unknown"
        writer = row["writer"] or "unknown"
        outcome = row["outcome"] or "pending"
        count = row["n"]

        cross = is_cross_tool(reader, writer)
        _tally(buckets["cross_tool" if cross else "same_tool"], outcome, count)

        pair = pairs.setdefault(
            (reader, writer),
            {"reader": reader, "writer": writer, "cross_tool": cross, **_empty()},
        )
        _tally(pair, outcome, count)

    for bucket in buckets.values():
        _finalise(bucket)
    for pair in pairs.values():
        _finalise(pair)

    return {
        **buckets,
        "pairs": sorted(pairs.values(), key=lambda p: -p["total"]),
    }


def _empty() -> dict:
    return {"used": 0, "rejected": 0, "unused": 0, "pending": 0,
            "judged": 0, "total": 0, "use_rate": 0.0}


def _tally(bucket: dict, outcome: str, count: int) -> None:
    bucket[outcome] = bucket.get(outcome, 0) + count
    bucket["total"] += count
    if outcome in JUDGED:
        bucket["judged"] += count


def _finalise(bucket: dict) -> None:
    bucket["use_rate"] = (
        round(100.0 * bucket["used"] / bucket["judged"], 1)
        if bucket["judged"] else 0.0
    )
