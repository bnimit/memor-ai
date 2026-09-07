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

#: An output token costs about five times a base input token across current
#: frontier models (Anthropic Sonnet/Opus and GPT-class alike). Output is
#: tracked because compression can *raise* it: shortening a prompt can strip
#: the context that kept an answer brief, and arXiv:2603.23527 measured output
#: expansion of up to 56x under aggressive compression, enough to swamp any
#: input saving. A ledger that only counts input tokens cannot see that
#: happening, which is the difference between reporting savings and reporting
#: cost.
OUTPUT_MULTIPLIER = 5.0


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
    #: Of those, the ones where something was actually compressed. This is the
    #: only population whose cache behaviour is attributable to compression:
    #: a passthrough request rewrote nothing, so its cache writes are the
    #: agent's own doing and say nothing about what compression cost.
    compressed_usage_requests: int = 0
    #: Output tokens the provider billed, split by whether the request was
    #: compressed. Kept apart because the comparison between them is the only
    #: in-ledger signal that compression changed how much the model wrote, and
    #: a single blended total would average that effect away.
    compressed_output: int = 0
    passthrough_output: int = 0
    compressed_output_requests: int = 0
    passthrough_output_requests: int = 0
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
    #: Savings from the PostToolUse hook path, which crushes tool output
    #: before it ever enters the transcript. Kept apart from proxy savings
    #: because it carries no cache risk: the payload is shrunk on its way in,
    #: so no already-cached prefix is rewritten.
    hook_requests: int = 0
    hook_before: int = 0
    hook_after: int = 0

    #: Randomized arms. Unlike every other comparison here these differ only in
    #: the treatment, so a gap between them is an effect rather than a
    #: correlation. Counted separately from the observational totals because
    #: mixing the two would forfeit exactly that property.
    arm_requests: dict = field(default_factory=dict)
    arm_billed_units: dict = field(default_factory=dict)
    arm_output: dict = field(default_factory=dict)
    arm_usage_requests: dict = field(default_factory=dict)

    @property
    def hook_saved(self) -> int:
        return max(0, self.hook_before - self.hook_after)

    @property
    def hook_pct(self) -> float:
        if self.hook_before <= 0:
            return 0.0
        return self.hook_saved / self.hook_before * 100

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
    def usage_coverage_pct(self) -> float:
        """Share of requests on which the provider reported usage at all."""
        if self.requests <= 0:
            return 0.0
        return self.usage_requests / self.requests * 100

    #: Below this, the cache overhead is drawn from too small a slice of
    #: traffic to be subtracted from savings measured across all of it.
    USAGE_COVERAGE_MIN_PCT = 80.0

    @property
    def compressed_requests(self) -> int:
        return max(0, self.requests - self.passthroughs)

    #: The overhead is only attributable to compression if a real share of the
    #: compressed requests reported usage themselves. One row out of hundreds
    #: is an accident, not a sample: the author's store had exactly one, which
    #: made a naive "greater than zero" check pass on a population that was
    #: otherwise entirely passthrough.
    ATTRIBUTABLE_MIN_PCT = 50.0

    @property
    def compressed_usage_pct(self) -> float:
        if not self.compressed_requests:
            return 0.0
        return self.compressed_usage_requests / self.compressed_requests * 100

    @property
    def usage_population_matches(self) -> bool:
        """Whether the usage sample overlaps the requests that were compressed.

        The net figure subtracts cache overhead from gross savings. Savings come
        from compressed requests; overhead comes from whichever requests
        reported usage. On the author's store those sets were almost disjoint --
        1,451 of 1,452 usage-bearing rows were **passthrough** -- so the
        subtraction charged compression for cache writes made by requests it
        never touched. Coverage alone cannot detect that: 26% coverage looks
        merely thin, not mismatched.
        """
        if not self.compressed_requests:
            return False
        return self.compressed_usage_pct >= self.ATTRIBUTABLE_MIN_PCT

    #: Both arms need at least this many observations before their means are
    #: worth comparing. Output length varies enormously per request, so a
    #: handful of rows either side says nothing.
    OUTPUT_COMPARISON_MIN = 20

    @property
    def mean_compressed_output(self) -> float:
        if not self.compressed_output_requests:
            return 0.0
        return self.compressed_output / self.compressed_output_requests

    @property
    def mean_passthrough_output(self) -> float:
        if not self.passthrough_output_requests:
            return 0.0
        return self.passthrough_output / self.passthrough_output_requests

    @property
    def output_comparable(self) -> bool:
        """Whether both arms carry enough billed output to compare."""
        return (
            self.compressed_output_requests >= self.OUTPUT_COMPARISON_MIN
            and self.passthrough_output_requests >= self.OUTPUT_COMPARISON_MIN
        )

    @property
    def output_expansion_pct(self) -> float:
        """How much longer the model's answer ran on compressed requests.

        Positive means compression made the model write more. This is an
        observational comparison, not a randomized one: compressed and
        passthrough requests differ in what they contained as well as in
        whether memor rewrote them, so a difference here is a signal to
        investigate rather than an effect estimate.
        """
        base = self.mean_passthrough_output
        if base <= 0 or not self.output_comparable:
            return 0.0
        return (self.mean_compressed_output - base) / base * 100

    @property
    def output_cost_units(self) -> float:
        """Billed output on compressed requests, in base-input equivalents."""
        return self.compressed_output * OUTPUT_MULTIPLIER

    #: Per-arm requests needed before a measured result is worth printing.
    #: Derived from this store's own variance: the per-request kept-ratio has a
    #: CV of 0.38, which needs ~57 observations per arm to detect a 20% effect
    #: at 80% power. Session-level assignment would have needed ~790.
    ARM_MIN_REQUESTS = 57

    @property
    def measured_arms_ready(self) -> bool:
        from memor.proxy.experiment import ARM_CONTROL, ARM_TREATMENT

        return all(
            self.arm_usage_requests.get(arm, 0) >= self.ARM_MIN_REQUESTS
            for arm in (ARM_CONTROL, ARM_TREATMENT)
        )

    @property
    def measured_saving_pct(self) -> float:
        """Billed cost difference between the randomized arms.

        Positive means the compressed arm cost less. This is the only figure
        here that is causal: assignment was random, so the arms differ in the
        treatment and nothing else. It prices input, cache and output together,
        because compression that shortens a prompt and lengthens an answer has
        to be able to come out negative.
        """
        from memor.proxy.experiment import ARM_CONTROL, ARM_TREATMENT

        control_n = self.arm_usage_requests.get(ARM_CONTROL, 0)
        treat_n = self.arm_usage_requests.get(ARM_TREATMENT, 0)
        if not control_n or not treat_n:
            return 0.0
        control = self.arm_billed_units.get(ARM_CONTROL, 0.0) / control_n
        treat = self.arm_billed_units.get(ARM_TREATMENT, 0.0) / treat_n
        if control <= 0:
            return 0.0
        return (control - treat) / control * 100

    @property
    def net_is_reliable(self) -> bool:
        """Whether the net figure rests on comparable populations.

        ``net_saved_units`` subtracts cache overhead from gross savings, but
        the two are measured over different request sets whenever usage is
        reported on only some requests: overhead from that subset, savings
        from all of them. Understating the subtrahend inflates the net, so a
        figure built that way is labelled rather than quietly presented.
        """
        return (
            self.cache_writes_observed
            and self.usage_population_matches
            and self.usage_coverage_pct >= self.USAGE_COVERAGE_MIN_PCT
        )

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
        if row.get("provider") == "hook":
            s.hook_requests += 1
            s.hook_before += before
            s.hook_after += after

        # Usage is absent on rows written before streaming usage was captured,
        # and on providers that only report it when the client opts in. Those
        # rows must not be counted as "observed zero cache", which would make
        # caching look absent rather than unmeasured.
        upstream_in = row.get("upstream_input_tokens")
        cache_read = row.get("upstream_cache_read_tokens")
        cache_creation = row.get("upstream_cache_creation_tokens")
        if any(v is not None for v in (upstream_in, cache_read, cache_creation)):
            s.usage_requests += 1
            if not row.get("passthrough"):
                s.compressed_usage_requests += 1
            s.upstream_input += int(upstream_in or 0)
            s.cache_read += int(cache_read or 0)
            s.cache_creation += int(cache_creation or 0)
            if cache_creation is not None:
                s.cache_write_observations += 1

        output = row.get("upstream_output_tokens")
        if output is not None:
            if row.get("passthrough"):
                s.passthrough_output += int(output)
                s.passthrough_output_requests += 1
            else:
                s.compressed_output += int(output)
                s.compressed_output_requests += 1

        # Randomized arms, tallied only when the row carries billed numbers.
        # A request the provider never reported on contributes nothing to a
        # cost comparison, and counting it would dilute the arm it landed in.
        arm = row.get("experiment_arm")
        if arm:
            s.arm_requests[arm] = s.arm_requests.get(arm, 0) + 1
            if upstream_in is not None or cache_read is not None:
                billed = (
                    int(upstream_in or 0)
                    + int(cache_read or 0) * CACHE_READ_MULTIPLIER
                    + int(cache_creation or 0) * CACHE_WRITE_MULTIPLIER
                    + int(output or 0) * OUTPUT_MULTIPLIER
                )
                s.arm_billed_units[arm] = s.arm_billed_units.get(arm, 0.0) + billed
                s.arm_output[arm] = s.arm_output.get(arm, 0) + int(output or 0)
                s.arm_usage_requests[arm] = s.arm_usage_requests.get(arm, 0) + 1

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
                "provider",
                "upstream_input_tokens",
                "upstream_cache_read_tokens",
                "upstream_cache_creation_tokens",
                # Both of these were written to the ledger and never selected
                # here, so the columns existed while every report that depended
                # on them silently saw None. Output is what prices an answer
                # that grew; the arm is what makes a comparison causal.
                "upstream_output_tokens",
                "experiment_arm",
            ) if c in have
        ]
        columns = ", ".join(
            ["agent", "tokens_before", "tokens_after", "content_types",
             "passthrough"]
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
        lines.append("No compression recorded yet.")
        lines.append("")
        lines.append("Two paths, both opt-in:")
        lines.append("  memor install-compress-hook          "
                     "crush tool output before it enters")
        lines.append("                                       "
                     "the transcript (Claude Code)")
        lines.append("  memor install-proxy --agent <name>   "
                     "compress whole requests in flight")
        lines.append("")
        lines.append("Already installed one? Nothing is recorded until the agent")
        lines.append("runs a command whose output is large enough to compress,")
        lines.append("and Claude Code must be restarted after installing a hook.")
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
    if summary.hook_requests:
        # The headline sums every ledger row, and hook rows are in there
        # despite never having been proxied. Saying so matters most when the
        # hook is carrying the number: this store's proxy has recorded nothing
        # since 2026-08-22, so a reader seeing "proxied tokens" would credit
        # the proxy for savings the hook produced.
        lines.append(
            f"  Includes {summary.hook_requests:,} hook rows that never went"
            " through the proxy;"
        )
        lines.append("  see HOOK PATH below for what they can and cannot prove.")
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
    if summary.hook_requests:
        lines.append("")
        lines.append(
            f"HOOK PATH (tool output crushed before it enters the transcript): "
            f"{summary.hook_pct:.1f}%"
        )
        lines.append(
            f"  {summary.hook_requests:,} tool results, "
            f"{summary.hook_before:,} -> {summary.hook_after:,} tokens"
        )
        lines.append(
            "  No cache risk on this path: the payload is shrunk on the way in,"
        )
        lines.append("  so no already-cached prefix is rewritten.")
        # Said plainly because this is the number most likely to be quoted, and
        # it is the one no provider will ever corroborate. The hook rewrites
        # tool output before the agent builds a request, so there is no billed
        # counterfactual: the provider never saw the original and cannot report
        # what it would have cost. Proxy rows can be grounded against
        # provider-reported usage; these cannot, ever, by construction.
        lines.append(
            "  Tokenizer estimate, not a billed measurement: the provider never"
        )
        lines.append(
            "  saw the uncompressed payload, so no invoice can confirm this."
        )
    lines.extend(_measured_lines(summary))
    lines.extend(_output_lines(summary))
    lines.append("")
    lines.append("  Says nothing about answer quality.")
    return lines


def _measured_lines(summary: CompressionSummary) -> list[str]:
    """The randomized result: the only causal number in this report.

    Everything else compares requests that differ in content as well as in
    treatment. Here assignment was random, so the arms differ only in whether
    memor rewrote the payload, and the gap between their billed cost is an
    effect rather than a correlation.
    """
    from memor.proxy.experiment import ARM_CONTROL, ARM_TREATMENT

    if not summary.arm_requests:
        return []

    control_n = summary.arm_usage_requests.get(ARM_CONTROL, 0)
    treat_n = summary.arm_usage_requests.get(ARM_TREATMENT, 0)
    lines = ["", "MEASURED (randomized holdout)"]

    if not summary.measured_arms_ready:
        lines.append(
            f"  {treat_n:,} compressed and {control_n:,} held-out requests have"
            " billed numbers;"
        )
        lines.append(
            f"  {summary.ARM_MIN_REQUESTS} per arm are needed before the"
            " difference means anything."
        )
        lines.append(
            "  Until then the figures above remain estimates, not measurements."
        )
        return lines

    pct = summary.measured_saving_pct
    if pct >= 0:
        lines.append(
            f"  Compressed requests cost {pct:.1f}% less than held-out ones,"
        )
    else:
        lines.append(
            f"  Compressed requests cost {abs(pct):.1f}% MORE than held-out ones,"
        )
    lines.append(
        f"  over {treat_n:,} compressed and {control_n:,} held-out requests,"
    )
    lines.append(
        "  pricing input, cache and output together as the provider billed them."
    )
    if pct < 0:
        lines.append(
            "  VERDICT: compression is costing money on this traffic. Output"
        )
        lines.append(
            "  growth or cache re-formation is outweighing the input saved."
        )
    return lines


def _output_lines(summary: CompressionSummary) -> list[str]:
    """What the model wrote back, which the input-only ledger cannot see.

    Output is billed at roughly five times input, so a compression that
    shortens the prompt and lengthens the answer can cost money while every
    input-side figure above reports a saving. arXiv:2603.23527 measured
    exactly that, up to 56x expansion on one benchmark. This section exists so
    the ledger can no longer be silent about it.
    """
    if not summary.compressed_output_requests:
        return []

    lines = ["", "OUTPUT TOKENS (billed at ~5x input)"]
    lines.append(
        f"  compressed requests: {summary.compressed_output:,} tokens over "
        f"{summary.compressed_output_requests:,} requests "
        f"({summary.mean_compressed_output:,.0f} mean)"
    )
    if summary.passthrough_output_requests:
        lines.append(
            f"  passthrough requests: {summary.passthrough_output:,} tokens over "
            f"{summary.passthrough_output_requests:,} requests "
            f"({summary.mean_passthrough_output:,.0f} mean)"
        )

    if not summary.output_comparable:
        lines.append(
            "  Too few observations on one side to compare the two, so whether"
        )
        lines.append(
            "  compression changed answer length is unmeasured, not zero."
        )
        return lines

    expansion = summary.output_expansion_pct
    if expansion > 0:
        lines.append(
            f"  Answers ran {expansion:.0f}% longer on compressed requests, costing"
            f" ~{summary.mean_compressed_output - summary.mean_passthrough_output:,.0f}"
        )
        lines.append(
            f"  extra output tokens each — about"
            f" {(summary.mean_compressed_output - summary.mean_passthrough_output) * OUTPUT_MULTIPLIER:,.0f}"
            " base-input equivalents."
        )
    else:
        lines.append(
            f"  Answers ran {abs(expansion):.0f}% shorter on compressed requests."
        )
    lines.append(
        "  Observational, not randomized: the two sets differ in content as"
    )
    lines.append(
        "  well as in treatment, so this flags a question rather than settles it."
    )
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
    elif not summary.usage_population_matches and summary.passthroughs:
        # Ordered before the coverage warning only when passthrough traffic is
        # present, because that is what makes the populations differ in kind
        # rather than in size. With no passthrough rows a low attributable
        # share is simply thin coverage, and the sample-size wording below is
        # the more useful diagnosis.
        #
        # The sharper failure, and the one a coverage percentage hides: the
        # overhead is not merely from fewer requests, it is from requests that
        # were never compressed. Nothing here is attributable to compression.
        lines.append(
            f"  NOT ATTRIBUTABLE: only {summary.compressed_usage_requests:,} of"
            f" {summary.compressed_requests:,} compressed"
        )
        lines.append(
            f"  requests reported usage ({summary.compressed_usage_pct:.0f}%)."
            " The cache activity above is"
        )
        lines.append(
            "  almost entirely passthrough traffic, which rewrote nothing, so it"
        )
        lines.append(
            "  cannot price what compression cost. Ignore the net figure until"
        )
        lines.append("  compressed requests report usage of their own.")
    elif not summary.net_is_reliable:
        lines.append(
            f"  Usage was reported on only {summary.usage_coverage_pct:.0f}% of"
            " requests, so the overhead"
        )
        lines.append(
            "  subtracted here comes from a smaller population than the savings"
        )
        lines.append("  it is subtracted from. The net figure is an upper bound.")
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
