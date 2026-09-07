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

#: The harness whose transcripts each reading agent produces. Cursor used to map
#: to ``claude_code`` because it had no ingest source of its own and could only
#: read another tool's store. It now writes its own artifacts (``source:
#: "cursor"``, read from Cursor's composer store), so mapping it to
#: ``claude_code`` would score a Cursor recall of a Cursor memory as cross-tool
#: and inflate the one number this module exists to keep honest.
READER_TO_WRITER = {
    "claude": "claude_code",
    "cursor": "cursor",
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
        "projects": _by_project(store),
    }


def _by_project(store: SqliteStore) -> list[dict]:
    """Where sharing actually happens.

    The reader × writer matrix answers whether tools share; it cannot say
    where. That distinction decides how a low cross-tool rate should be read.
    On the author's machine only 2 of 27 projects have more than one agent
    active, so the overall figure is dominated by projects where sharing was
    never possible -- an under-exercised feature, not a broken one.
    """
    rows = store.db.execute(
        """
        SELECT rl.project AS project, rl.agent AS reader, a.source AS writer,
               ro.outcome AS outcome, COUNT(*) AS n
        FROM recall_outcomes ro
        JOIN recall_log rl ON rl.id = ro.recall_id
        JOIN artifacts a   ON a.id  = ro.artifact_id
        GROUP BY rl.project, reader, writer, outcome
        """
    ).fetchall()

    projects: dict[str, dict] = {}
    readers: dict[str, set] = {}

    for row in rows:
        name = row["project"] or "unknown"
        entry = projects.setdefault(
            name, {"project": name, "cross_tool": 0, "same_tool": 0, **_empty()}
        )
        readers.setdefault(name, set()).add(row["reader"] or "unknown")

        count = row["n"]
        if is_cross_tool(row["reader"] or "", row["writer"] or ""):
            entry["cross_tool"] += count
        else:
            entry["same_tool"] += count
        _tally(entry, row["outcome"] or "pending", count)

    for name, entry in projects.items():
        _finalise(entry)
        # "Shared" means more than one agent read here, which is the condition
        # under which a cross-tool figure is meaningful at all.
        entry["agents"] = sorted(readers[name])
        entry["shared"] = len(readers[name]) > 1

    return sorted(projects.values(), key=lambda p: (-p["cross_tool"], -p["total"]))


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
