"""Realized compression savings, reported from the ledger.

The gap this closes: a build log compresses 97% on a sample, but if most real
requests carry nothing compressible the realized figure is a fraction of that.
Only the ledger knows which is true.
"""
from __future__ import annotations

import json

from memor.compression_worth import (
    MIN_REQUESTS,
    format_report,
    load_savings_rows,
    summarize_savings,
)


def _row(agent="claude", before=1000, after=400, passthrough=0, types=None):
    return {
        "agent": agent,
        "tokens_before": before,
        "tokens_after": after,
        "passthrough": passthrough,
        "content_types": json.dumps(types or {"log": 1}),
    }


# --- aggregation -------------------------------------------------------------


def test_totals_and_realized_rate():
    s = summarize_savings([_row(before=1000, after=400) for _ in range(30)])
    assert s.requests == 30
    assert s.tokens_before == 30_000
    assert s.saved == 18_000
    assert round(s.realized_pct, 1) == 60.0


def test_passthrough_rate_is_reported():
    rows = [_row(passthrough=1) for _ in range(27)] + [_row() for _ in range(3)]
    s = summarize_savings(rows)
    assert s.passthroughs == 27
    assert round(s.passthrough_pct) == 90


def test_content_types_are_counted_across_rows():
    rows = [_row(types={"log": 2}), _row(types={"code": 1, "log": 1})]
    s = summarize_savings(rows)
    assert s.by_type == {"log": 3, "code": 1}


def test_per_agent_breakdown():
    rows = [_row(agent="claude") for _ in range(3)] + [_row(agent="cursor")]
    s = summarize_savings(rows)
    assert s.by_agent["claude"]["requests"] == 3
    assert s.by_agent["cursor"]["requests"] == 1


def test_content_types_may_arrive_as_a_dict():
    s = summarize_savings([{**_row(), "content_types": {"code": 4}}])
    assert s.by_type == {"code": 4}


def test_malformed_content_types_do_not_raise():
    s = summarize_savings([{**_row(), "content_types": "{not json"}])
    assert s.requests == 1
    assert s.by_type == {}


def test_missing_fields_default_to_zero():
    s = summarize_savings([{}])
    assert s.requests == 1
    assert s.tokens_before == 0
    assert s.realized_pct == 0.0


def test_savings_never_go_negative():
    """A compressor that grew the payload must not report negative savings."""
    s = summarize_savings([_row(before=100, after=180)])
    assert s.saved == 0


# --- reporting discipline ----------------------------------------------------


def test_small_samples_refuse_to_report_a_rate():
    text = "\n".join(format_report(summarize_savings([_row() for _ in range(3)])))
    assert "too few requests" in text
    assert "REALIZED SAVINGS" not in text


def test_scored_report_states_the_rate_and_its_limits():
    rows = [_row() for _ in range(MIN_REQUESTS + 5)]
    text = "\n".join(format_report(summarize_savings(rows)))
    assert "REALIZED SAVINGS" in text
    # The two things a savings number must never be quoted without. The cache
    # caveat used to be a fixed disclaimer ("Gross, not net"); it is now a
    # measured section, which states "unmeasured" only when the provider
    # reported no usage. Either way it must appear.
    assert "NET OF CACHE" in text
    assert "quality" in text


def test_report_names_coverage_as_the_cap():
    rows = [_row(passthrough=1) for _ in range(MIN_REQUESTS)] + [_row()]
    text = "\n".join(format_report(summarize_savings(rows)))
    assert "carried nothing compressible" in text


def test_empty_ledger_points_at_the_opt_in():
    """An empty report must name every way to make it non-empty.

    It previously named only the proxy, so a user who had just run
    `memor install-compress-hook` was told to install something else.
    """
    text = "\n".join(format_report(summarize_savings([])))
    assert "No compression recorded yet" in text
    assert "install-proxy" in text
    assert "install-compress-hook" in text
    # The most common reason for an empty report is a hook installed but not
    # yet loaded, which no amount of further installing will fix.
    assert "restarted" in text


# --- loading -----------------------------------------------------------------


def test_missing_database_returns_no_rows(tmp_path):
    assert load_savings_rows(str(tmp_path / "nope.db")) == []


def test_reads_real_ledger_rows(tmp_path):
    import sqlite3
    import time

    path = tmp_path / "m.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE proxy_savings(timestamp REAL, agent TEXT, tokens_before INT,"
        " tokens_after INT, content_types TEXT, passthrough INT)"
    )
    db.execute(
        "INSERT INTO proxy_savings VALUES(?,?,?,?,?,?)",
        (time.time(), "claude", 500, 200, '{"log": 1}', 0),
    )
    db.commit()
    db.close()
    rows = load_savings_rows(str(path))
    assert len(rows) == 1
    assert rows[0]["agent"] == "claude"


def test_rows_outside_the_window_are_excluded(tmp_path):
    import sqlite3
    import time

    path = tmp_path / "m.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE proxy_savings(timestamp REAL, agent TEXT, tokens_before INT,"
        " tokens_after INT, content_types TEXT, passthrough INT)"
    )
    db.execute(
        "INSERT INTO proxy_savings VALUES(?,?,?,?,?,?)",
        (time.time() - 90 * 86400, "claude", 500, 200, "{}", 0),
    )
    db.commit()
    db.close()
    assert load_savings_rows(str(path), days=30) == []


# --- is the experiment actually running? -------------------------------------

from memor.compression_worth import liveness  # noqa: E402


def _summary(requests, code=0):
    rows = [_row(types={"code:go": 1}) for _ in range(code)]
    rows += [_row(types={"log": 1}) for _ in range(max(0, requests - code))]
    return summarize_savings(rows)


def test_disabled_reports_off():
    assert liveness(False, None, None)["state"] == "off"


def test_enabled_with_no_traffic_is_pending():
    assert liveness(True, 1000.0, _summary(0))["state"] == "pending"


def test_enabled_with_code_compressed_is_live():
    r = liveness(True, 1000.0, _summary(60, code=5))
    assert r["state"] == "live"
    assert "5" in r["detail"]


def test_enabled_but_nothing_compressed_after_enough_traffic_is_flagged():
    """The failure that costs a week: flag on, running build too old to act on it."""
    r = liveness(True, 1000.0, _summary(200, code=0))
    assert r["state"] == "not_taking_effect"
    assert "older build" in r["detail"]
    assert "pipx install --force" in r["detail"]


def test_small_traffic_does_not_cry_wolf():
    """Below the evidence threshold, absence of compression proves nothing."""
    assert liveness(True, 1000.0, _summary(5, code=0))["state"] == "pending"


def test_since_filter_reads_only_rows_after_the_boundary(tmp_path):
    import sqlite3
    import time

    path = tmp_path / "m.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE proxy_savings(timestamp REAL, agent TEXT, tokens_before INT,"
        " tokens_after INT, content_types TEXT, passthrough INT)"
    )
    now = time.time()
    db.executemany(
        "INSERT INTO proxy_savings VALUES(?,?,?,?,?,?)",
        [(now - 5000, "claude", 10, 5, "{}", 0), (now - 10, "claude", 10, 5, "{}", 0)],
    )
    db.commit()
    db.close()
    assert len(load_savings_rows(str(path), since=now - 100)) == 1


def test_hook_path_savings_are_reported_separately():
    """Hook savings and proxy savings are not the same kind of number.

    The hook shrinks a payload before it enters the transcript, so there is no
    cached prefix to invalidate; the proxy rewrites a payload that may already
    be cached. Blending them would attach the proxy's cache caveat to savings
    that do not carry it.
    """
    rows = [
        dict(agent="claude", provider="hook", tokens_before=1000,
             tokens_after=100, content_types={"log": 1}, passthrough=0)
        for _ in range(MIN_REQUESTS + 5)
    ]
    summary = summarize_savings(rows)
    assert summary.hook_requests == MIN_REQUESTS + 5
    assert summary.hook_pct == 90.0

    text = "\n".join(format_report(summary))
    assert "HOOK PATH" in text
    assert "No cache risk on this path" in text


def test_proxy_only_report_omits_the_hook_section():
    rows = [_row() for _ in range(MIN_REQUESTS + 5)]
    assert "HOOK PATH" not in "\n".join(format_report(summarize_savings(rows)))


# --- attribution: is the overhead even about compression? --------------------


def _usage_row(passthrough, before=1000, after=400, cache_creation=500):
    return {
        "agent": "claude",
        "tokens_before": before,
        "tokens_after": after,
        "passthrough": passthrough,
        "content_types": json.dumps({"log": 1}),
        "upstream_input_tokens": 900,
        "upstream_cache_read_tokens": 8000,
        "upstream_cache_creation_tokens": cache_creation,
    }


def test_overhead_from_passthrough_traffic_is_not_attributable():
    """The author's store: 1,451 of 1,452 usage rows rewrote nothing.

    Savings come from compressed requests, overhead from whichever requests
    reported usage. When those are different sets, the subtraction charges
    compression for cache writes it never caused.
    """
    rows = [_row() for _ in range(MIN_REQUESTS)]
    rows += [_usage_row(passthrough=1) for _ in range(50)]

    s = summarize_savings(rows)

    assert s.compressed_usage_requests == 0
    assert not s.usage_population_matches
    assert not s.net_is_reliable


def test_a_single_compressed_usage_row_is_not_a_sample():
    """Exactly the shape that made a naive >0 check pass on real data."""
    rows = [_row() for _ in range(MIN_REQUESTS)]
    rows += [_usage_row(passthrough=1) for _ in range(50)]
    rows += [_usage_row(passthrough=0)]

    s = summarize_savings(rows)

    assert s.compressed_usage_requests == 1
    assert not s.usage_population_matches


def test_usage_from_compressed_requests_is_attributable():
    rows = [_usage_row(passthrough=0) for _ in range(MIN_REQUESTS)]

    s = summarize_savings(rows)

    assert s.compressed_usage_pct == 100.0
    assert s.usage_population_matches
    assert s.net_is_reliable


def test_report_names_the_mismatch_rather_than_the_sample_size():
    """A coverage warning reads as "thin data"; this is a wrong population."""
    rows = [_row() for _ in range(MIN_REQUESTS)]
    rows += [_usage_row(passthrough=1) for _ in range(50)]

    text = "\n".join(format_report(summarize_savings(rows)))

    assert "NOT ATTRIBUTABLE" in text
    assert "passthrough traffic" in text


def test_attributable_traffic_reports_no_mismatch_warning():
    rows = [_usage_row(passthrough=0) for _ in range(MIN_REQUESTS)]

    text = "\n".join(format_report(summarize_savings(rows)))

    assert "NOT ATTRIBUTABLE" not in text


def test_thin_coverage_without_passthrough_reads_as_sample_size():
    """Both warnings can be true; the useful one depends on why they differ.

    With no passthrough traffic there is no wrong population, only a small
    one, so the sample-size wording is the more actionable diagnosis.
    """
    rows = [_usage_row(passthrough=0) for _ in range(10)]
    rows += [_row() for _ in range(90)]

    text = "\n".join(format_report(summarize_savings(rows)))

    assert "upper bound" in text
    assert "NOT ATTRIBUTABLE" not in text


# --- what each figure can actually prove -------------------------------------


def _hook_row(before=1000, after=100):
    return dict(agent="claude", provider="hook", tokens_before=before,
                tokens_after=after, content_types={"log": 1}, passthrough=0)


def test_hook_savings_are_labelled_as_unverifiable():
    """No invoice can ever confirm this number.

    The hook rewrites tool output before the agent builds a request, so the
    provider never saw the original and cannot report what it would have cost.
    Proxy rows can be grounded against provider-reported usage; these cannot,
    which makes this the number most likely to be quoted and least likely to
    be checkable.
    """
    text = "\n".join(format_report(
        summarize_savings([_hook_row() for _ in range(MIN_REQUESTS + 5)])))

    assert "Tokenizer estimate, not a billed measurement" in text


def test_the_headline_discloses_hook_rows_it_counts_as_proxied():
    """"% of proxied tokens" sums hook rows that never touched the proxy."""
    rows = [_row() for _ in range(MIN_REQUESTS)]
    rows += [_hook_row() for _ in range(7)]

    text = "\n".join(format_report(summarize_savings(rows)))

    assert "Includes 7 hook rows that never went through the proxy" in text


def test_a_proxy_only_headline_makes_no_hook_disclosure():
    text = "\n".join(format_report(
        summarize_savings([_row() for _ in range(MIN_REQUESTS)])))

    assert "hook rows that never went through the proxy" not in text


# --- output tokens: the cost an input-only ledger cannot see -----------------


def _out_row(passthrough, output, before=1000, after=400):
    return {
        "agent": "claude", "tokens_before": before, "tokens_after": after,
        "passthrough": passthrough, "content_types": json.dumps({"log": 1}),
        "upstream_input_tokens": 900, "upstream_cache_read_tokens": 100,
        "upstream_cache_creation_tokens": 50, "upstream_output_tokens": output,
    }


def test_output_expansion_is_reported_as_a_cost():
    """arXiv:2603.23527 measured up to 56x output expansion under compression.

    Output bills at ~5x input, so a shorter prompt with a longer answer can
    cost money while every input-side figure reports a saving. The ledger
    stored upstream_output_tokens and never read it, so this was invisible.
    """
    rows = [_out_row(0, 3000) for _ in range(30)]
    rows += [_out_row(1, 500, after=1000) for _ in range(30)]

    s = summarize_savings(rows)

    assert s.output_comparable
    assert round(s.output_expansion_pct) == 500
    text = "\n".join(format_report(s))
    assert "500% longer on compressed requests" in text
    assert "base-input equivalents" in text


def test_shorter_answers_are_reported_without_alarm():
    rows = [_out_row(0, 400) for _ in range(30)]
    rows += [_out_row(1, 800, after=1000) for _ in range(30)]

    text = "\n".join(format_report(summarize_savings(rows)))

    assert "50% shorter on compressed requests" in text


def test_a_thin_arm_reports_unmeasured_rather_than_zero():
    """One side with a handful of rows cannot support a comparison."""
    rows = [_out_row(0, 3000) for _ in range(30)]
    rows += [_out_row(1, 500, after=1000) for _ in range(3)]

    s = summarize_savings(rows)

    assert not s.output_comparable
    assert s.output_expansion_pct == 0.0
    text = "\n".join(format_report(s))
    assert "unmeasured, not zero" in text


def test_the_comparison_is_labelled_observational():
    """Compressed and passthrough requests differ in content, not just treatment."""
    rows = [_out_row(0, 3000) for _ in range(30)]
    rows += [_out_row(1, 500, after=1000) for _ in range(30)]

    text = "\n".join(format_report(summarize_savings(rows)))

    assert "Observational, not randomized" in text


def test_no_output_data_produces_no_output_section():
    rows = [_row() for _ in range(MIN_REQUESTS)]

    assert "OUTPUT TOKENS" not in "\n".join(format_report(summarize_savings(rows)))
