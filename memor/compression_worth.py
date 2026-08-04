"""Realized compression savings, from the ledger rather than from samples.

Synthetic numbers are easy and misleading: a build log compresses 97%, but if
97% of real requests carry nothing compressible the realized figure is 8%. This
reports what actually happened on real traffic, by content type, so the number
on the dashboard is one that survived contact with the user's own work.

What it deliberately does not claim:

* **Cache re-formation cost is not directly observable.** Compressing a payload
  that appears in more than one request changes the prompt prefix once, and the
  provider re-caches. Content-addressed CCR ids make that a one-time cost rather
  than a per-request one, but the size of it is not in our ledger — only the
  provider knows. Savings here are gross, not net.
* **Nothing about answer quality.** Published work finds code compression can
  move task success in either direction. Token savings are not evidence of
  neutral quality.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

#: Below this many requests the realized rate is too noisy to report.
MIN_REQUESTS = 25


@dataclass
class TypeStats:
    content_type: str = ""
    occurrences: int = 0


@dataclass
class CompressionSummary:
    requests: int = 0
    passthroughs: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    by_agent: dict[str, dict] = field(default_factory=dict)

    @property
    def saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    @property
    def realized_pct(self) -> float:
        if self.tokens_before <= 0:
            return 0.0
        return self.saved / self.tokens_before * 100

    @property
    def passthrough_pct(self) -> float:
        if self.requests <= 0:
            return 0.0
        return self.passthroughs / self.requests * 100

    @property
    def scored(self) -> bool:
        return self.requests >= MIN_REQUESTS


def summarize_savings(rows: list[dict]) -> CompressionSummary:
    """Aggregate proxy_savings rows into a realized-savings summary."""
    s = CompressionSummary()
    for row in rows:
        s.requests += 1
        if row.get("passthrough"):
            s.passthroughs += 1
        before = int(row.get("tokens_before") or 0)
        after = int(row.get("tokens_after") or 0)
        s.tokens_before += before
        s.tokens_after += after

        agent = row.get("agent") or "unknown"
        bucket = s.by_agent.setdefault(
            agent, {"requests": 0, "tokens_before": 0, "tokens_after": 0}
        )
        bucket["requests"] += 1
        bucket["tokens_before"] += before
        bucket["tokens_after"] += after

        types = row.get("content_types")
        if isinstance(types, str):
            try:
                types = json.loads(types)
            except (json.JSONDecodeError, ValueError):
                types = {}
        for name, count in (types or {}).items():
            s.by_type[name] = s.by_type.get(name, 0) + int(count)
    return s


def load_savings_rows(db_path: str, *, days: int = 30) -> list[dict]:
    """Read proxy_savings rows from the ledger, read-only."""
    import sqlite3
    import time
    from pathlib import Path

    path = Path(db_path)
    if not path.exists():
        return []
    cutoff = time.time() - days * 86400
    try:
        db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT agent, tokens_before, tokens_after, content_types, passthrough "
            "FROM proxy_savings WHERE timestamp >= ?",
            (cutoff,),
        ).fetchall()
        db.close()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def format_report(summary: CompressionSummary, *, days: int = 30) -> list[str]:
    lines = [f"memor compression — realized savings ({days}d)", "=" * 58]
    if summary.requests == 0:
        lines.append("No proxied requests recorded.")
        lines.append("")
        lines.append("The proxy is opt-in: memor install-proxy --agent <name>")
        return lines

    lines.append(
        f"requests={summary.requests:,}  "
        f"passthrough={summary.passthroughs:,} ({summary.passthrough_pct:.0f}%)"
    )
    lines.append(
        f"tokens {summary.tokens_before:,} -> {summary.tokens_after:,}  "
        f"saved {summary.saved:,}"
    )
    lines.append("")

    if summary.by_type:
        lines.append("Compressor that fired (by payload count):")
        for name, count in sorted(
            summary.by_type.items(), key=lambda kv: kv[1], reverse=True
        ):
            lines.append(f"  {count:>7,}  {name}")
        lines.append("")

    if summary.by_agent:
        lines.append(f"{'agent':<14}{'reqs':>8}{'before':>12}{'after':>12}{'saved':>8}")
        for agent, d in sorted(
            summary.by_agent.items(), key=lambda kv: kv[1]["tokens_before"], reverse=True
        ):
            before, after = d["tokens_before"], d["tokens_after"]
            pct = (1 - after / before) * 100 if before else 0.0
            lines.append(
                f"{agent[:13]:<14}{d['requests']:>8,}{before:>12,}{after:>12,}{pct:>7.1f}%"
            )
        lines.append("")

    lines.append("=" * 58)
    if not summary.scored:
        lines.append(
            f"VERDICT: too few requests to report a rate (need {MIN_REQUESTS})"
        )
        return lines

    lines.append(f"REALIZED SAVINGS: {summary.realized_pct:.1f}% of proxied tokens")
    lines.append(
        f"  {summary.passthrough_pct:.0f}% of requests carried nothing compressible — "
        "coverage,"
    )
    lines.append("  not compressor quality, is what caps this number.")
    lines.append("")
    lines.append("  Gross, not net: compressing a payload that recurs across requests")
    lines.append("  re-forms the provider's prompt cache once, and that cost is not")
    lines.append("  observable from here. Says nothing about answer quality.")
    return lines


def report(db_path: str, *, days: int = 30) -> list[str]:
    return format_report(summarize_savings(load_savings_rows(db_path, days=days)), days=days)
