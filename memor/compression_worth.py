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

#: Provider list-price multipliers on a base input token. Anthropic bills a
#: cache write at 1.25x and a cache read at 0.1x; OpenAI discounts cached input
#: to roughly 0.1x and charges nothing extra to create it. Anthropic's is used
#: as the conservative default because it is the only one that can make
#: compression *lose* money, which is the case worth being able to detect.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.1


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
    #: Requests where the provider actually reported usage back to us.
    usage_requests: int = 0
    upstream_input: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    #: Requests that reported a cache-*write* figure specifically. A row
    #: written before the column existed reports reads but not writes, and
    #: counting its NULL as zero would price the one cost compression can
    #: actually add at nothing.
    cache_write_observations: int = 0
    #: Tokens on the subset of requests that carried something compressible.
    compressible_before: int = 0
    compressible_after: int = 0

    @property
    def saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    @property
    def realized_pct(self) -> float:
        if self.tokens_before <= 0:
            return 0.0
        return self.saved / self.tokens_before * 100

    @property
    def compressible_saved(self) -> int:
        return max(0, self.compressible_before - self.compressible_after)

    @property
    def compressible_pct(self) -> float:
        """Savings rate on requests that had anything to compress.

        The blended rate answers "what fraction of all proxied tokens did we
        remove", which is dominated by conversation history the compressor
        deliberately refuses to touch. This answers the different and more
        actionable question: when there *was* something to compress, how much
        came off. Coverage is reported separately rather than folded in, so a
        low number cannot be hidden inside a high one.
        """
        if self.compressible_before <= 0:
            return 0.0
        return self.compressible_saved / self.compressible_before * 100

    @property
    def has_usage(self) -> bool:
        return self.usage_requests > 0

    @property
    def cache_writes_observed(self) -> bool:
        return self.cache_write_observations > 0

    @property
    def cache_hit_pct(self) -> float:
        """Share of billed prompt tokens the provider served from cache."""
        total = self.upstream_input + self.cache_read + self.cache_creation
        if total <= 0:
            return 0.0
        return self.cache_read / total * 100

    @property
    def billed_input_units(self) -> float:
        """Prompt cost in base-input-token equivalents, as billed.

        A raw token count cannot answer whether compression saved money once
        caching is involved: 1,000 tokens read from cache cost a tenth of 1,000
        fresh ones, and 1,000 written to cache cost more. Weighting each class
        by its price is the only way the ledger can express that.
        """
        return (
            self.upstream_input
            + self.cache_read * CACHE_READ_MULTIPLIER
            + self.cache_creation * CACHE_WRITE_MULTIPLIER
        )

    @property
    def cache_overhead_units(self) -> float:
        """Extra cost of cache writes over reading the same tokens from cache.

        This is the quantity that compression can inflate. Rewriting a prefix
        that was being read from cache turns reads into writes, and if that
        overhead exceeds the tokens removed, compression is losing money even
        though gross savings look positive.
        """
        return self.cache_creation * (CACHE_WRITE_MULTIPLIER - CACHE_READ_MULTIPLIER)

    @property
    def net_saved_units(self) -> float:
        """Tokens removed, less the cache-write overhead they may have caused.

        Deliberately pessimistic: it charges compression for *every* cache
        write observed, including writes that would have happened anyway on a
        first request or after a provider-side eviction. A positive number here
        is therefore a floor on the true saving, not an estimate of it.
        """
        return self.saved - self.cache_overhead_units

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
        if not row.get("passthrough"):
            s.compressible_before += before
            s.compressible_after += after

        # Usage is absent on rows written before streaming usage was captured,
        # and on providers that only report it when the client opts in. Those
        # rows must not be counted as "observed zero cache", which would make
        # caching look absent rather than unmeasured.
        upstream_in = row.get("upstream_input_tokens")
        cache_read = row.get("upstream_cache_read_tokens")
        cache_creation = row.get("upstream_cache_creation_tokens")
        if any(v is not None for v in (upstream_in, cache_read, cache_creation)):
            s.usage_requests += 1
            s.upstream_input += int(upstream_in or 0)
            s.cache_read += int(cache_read or 0)
            s.cache_creation += int(cache_creation or 0)
            if cache_creation is not None:
                s.cache_write_observations += 1

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


def load_savings_rows(
    db_path: str, *, days: int = 30, since: float | None = None
) -> list[dict]:
    """Read proxy_savings rows from the ledger, read-only.

    ``since`` overrides ``days`` and is used to ask a sharper question: has
    anything actually been compressed *since the flag was switched on*.
    """
    import sqlite3
    import time
    from pathlib import Path

    path = Path(db_path)
    if not path.exists():
        return []
    cutoff = since if since is not None else time.time() - days * 86400
    try:
        db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        # Opened read-only, so a ledger predating the cache columns cannot be
        # migrated here; select them only when present rather than failing and
        # reporting nothing at all.
        have = {r["name"] for r in db.execute("PRAGMA table_info(proxy_savings)")}
        optional = [
            c for c in (
                "upstream_input_tokens",
                "upstream_cache_read_tokens",
                "upstream_cache_creation_tokens",
            ) if c in have
        ]
        columns = ", ".join(
            ["agent", "tokens_before", "tokens_after", "content_types", "passthrough"]
            + optional
        )
        rows = db.execute(
            f"SELECT {columns} FROM proxy_savings WHERE timestamp >= ?",
            (cutoff,),
        ).fetchall()
        db.close()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


#: Requests seen since the flag flipped before "nothing happened" means anything.
_LIVENESS_MIN_REQUESTS = 40


def liveness(
    enabled: bool, started_at: float | None, since_summary: CompressionSummary | None
) -> dict:
    """Is the experiment actually running, or only switched on in config?

    A flag can be true while the installed build predates the feature, or while
    the proxy has not been restarted. Both look identical to config and produce
    a week of silence before anyone notices. This compares intent against the
    ledger and says so.
    """
    if not enabled:
        return {"state": "off", "detail": "compression of older payloads is disabled"}
    if started_at is None:
        return {"state": "on", "detail": "enabled (no start time recorded)"}
    if since_summary is None or since_summary.requests == 0:
        return {
            "state": "pending",
            "detail": "enabled, but no proxied requests recorded yet",
        }
    code = sum(v for k, v in since_summary.by_type.items() if k.startswith("code"))
    if code == 0 and since_summary.requests >= _LIVENESS_MIN_REQUESTS:
        return {
            "state": "not_taking_effect",
            "detail": (
                f"enabled, but {since_summary.requests:,} requests have gone through "
                "with zero code payloads compressed — the running proxy is probably "
                "an older build. Reinstall and restart: "
                "pipx install --force . && memor service restart"
            ),
        }
    if code == 0:
        return {
            "state": "pending",
            "detail": f"enabled; {since_summary.requests:,} requests so far, none with code yet",
        }
    return {
        "state": "live",
        "detail": f"{code:,} code payloads compressed since enabled",
    }


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
    lines.append(
        f"ON COMPRESSIBLE REQUESTS: {summary.compressible_pct:.1f}% "
        f"({summary.compressible_before:,} -> {summary.compressible_after:,})"
    )
    lines.append(
        f"  coverage {100 - summary.passthrough_pct:.0f}% of requests. "
        "Raising coverage, not the rate,"
    )
    lines.append("  is the larger lever.")
    lines.append("")
    lines.extend(_cache_lines(summary))
    lines.append("")
    lines.append("  Says nothing about answer quality.")
    return lines


def _cache_lines(summary: CompressionSummary) -> list[str]:
    """The net-of-cache verdict, or an honest statement that it is unknown."""
    if not summary.has_usage:
        return [
            "NET OF CACHE: unmeasured — the provider reported no usage on any",
            "  request in this window. Gross savings above may overstate the",
            "  real figure, because rewriting a cached prefix forces the",
            "  provider to re-cache it at a higher per-token price.",
        ]
    coverage = summary.usage_requests / summary.requests * 100 if summary.requests else 0.0
    net = summary.net_saved_units
    lines = [
        f"NET OF CACHE: {net:,.0f} base-token equivalents saved "
        f"({summary.usage_requests:,} of {summary.requests:,} "
        f"requests reported usage, {coverage:.0f}%)",
        f"  cache reads {summary.cache_read:,} ({summary.cache_hit_pct:.0f}% of billed prompt)"
        f"  writes {summary.cache_creation:,}",
        f"  cache-write overhead charged against savings: "
        f"{summary.cache_overhead_units:,.0f}",
    ]
    if not summary.cache_writes_observed:
        lines.append(
            "  Cache writes were never reported on these rows, so the overhead"
        )
        lines.append(
            "  above is a floor of zero rather than a measurement. Treat the"
        )
        lines.append("  net figure as provisional until fresh traffic accumulates.")
    if net <= 0:
        lines.append(
            "  VERDICT: compression is not paying for itself once cache"
        )
        lines.append(
            "  re-formation is priced in. Every cache write in the window is"
        )
        lines.append(
            "  charged here, including ones compression did not cause, so this"
        )
        lines.append("  is a lower bound — but it is not a number to advertise.")
    return lines


def report(db_path: str, *, days: int = 30) -> list[str]:
    return format_report(summarize_savings(load_savings_rows(db_path, days=days)), days=days)
