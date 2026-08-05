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
    # The two things a savings number must never be quoted without.
    assert "Gross, not net" in text
    assert "quality" in text


def test_report_names_coverage_as_the_cap():
    rows = [_row(passthrough=1) for _ in range(MIN_REQUESTS)] + [_row()]
    text = "\n".join(format_report(summarize_savings(rows)))
    assert "carried nothing compressible" in text


def test_empty_ledger_points_at_the_opt_in():
    text = "\n".join(format_report(summarize_savings([])))
    assert "No proxied requests recorded" in text
    assert "install-proxy" in text


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
