"""Upstream usage capture and net-of-cache accounting.

These exist because the ledger's four ``upstream_*`` columns were NULL on all
5,414 rows of the development machine's real traffic. The cause was structural,
not a bug in one branch: usage arrives after the response headers, and the
streaming path wrote its ledger row before forwarding a single byte.
"""
from __future__ import annotations

import time

from memor.compression_worth import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    summarize_savings,
    format_report,
)
from memor.proxy.usage import (
    UsageSniffer,
    usage_from_anthropic,
    usage_from_openai,
)
from memor.store.sqlite_store import SqliteStore


# --- SSE sniffing -----------------------------------------------------------

def test_anthropic_stream_usage_spans_two_frames():
    """input/cache counts arrive in message_start, output in message_delta."""
    sniffer = UsageSniffer("anthropic")
    sniffer.feed(
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"usage":'
        b'{"input_tokens":120,"cache_read_input_tokens":9000,'
        b'"cache_creation_input_tokens":400,"output_tokens":1}}}\n\n'
    )
    sniffer.feed(
        b'event: message_delta\n'
        b'data: {"type":"message_delta","usage":{"output_tokens":88}}\n\n'
    )
    sniffer.close()

    assert sniffer.usage.input_tokens == 120
    assert sniffer.usage.cache_read_tokens == 9000
    assert sniffer.usage.cache_creation_tokens == 400
    # The later frame's output count must win over the placeholder.
    assert sniffer.usage.output_tokens == 88


def test_sniffer_reassembles_frame_split_across_chunks():
    """A data: line is not guaranteed to arrive whole.

    Parsing each chunk independently silently yields nothing, which is exactly
    the failure mode that leaves a column NULL without anyone noticing.
    """
    frame = (
        b'data: {"type":"message_start","message":{"usage":'
        b'{"input_tokens":50,"cache_read_input_tokens":10}}}\n'
    )
    sniffer = UsageSniffer("anthropic")
    for i in range(0, len(frame), 7):
        sniffer.feed(frame[i:i + 7])
    sniffer.close()
    assert sniffer.usage.input_tokens == 50
    assert sniffer.usage.cache_read_tokens == 10


def test_sniffer_flushes_frame_without_trailing_newline():
    sniffer = UsageSniffer("openai")
    sniffer.feed(b'data: {"usage":{"prompt_tokens":30,"completion_tokens":4}}')
    assert sniffer.usage.input_tokens is None  # not yet flushed
    sniffer.close()
    assert sniffer.usage.input_tokens == 30
    assert sniffer.usage.output_tokens == 4


def test_sniffer_ignores_done_and_malformed_frames():
    sniffer = UsageSniffer("openai")
    sniffer.feed(b'data: [DONE]\n')
    sniffer.feed(b'data: {not json\n')
    sniffer.feed(b': comment line\n')
    sniffer.feed(b'data: "a string, not an object"\n')
    sniffer.close()
    assert sniffer.usage.is_empty()


def test_openai_cached_tokens_are_split_out_of_prompt_tokens():
    """OpenAI counts cached tokens inside prompt_tokens; Anthropic does not.

    Without normalizing, the same cached token would be counted once for
    Anthropic and twice for OpenAI, and the cost model would silently differ
    by provider.
    """
    usage = usage_from_openai({
        "usage": {
            "prompt_tokens": 1000,
            "prompt_tokens_details": {"cached_tokens": 900},
            "completion_tokens": 20,
        }
    })
    assert usage.input_tokens == 100
    assert usage.cache_read_tokens == 900
    assert usage.output_tokens == 20


def test_openai_without_details_reports_prompt_tokens_as_uncached():
    usage = usage_from_openai({"usage": {"prompt_tokens": 42}})
    assert usage.input_tokens == 42
    assert usage.cache_read_tokens is None


def test_anthropic_non_streaming_body():
    usage = usage_from_anthropic({
        "usage": {
            "input_tokens": 7,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 2,
            "output_tokens": 5,
        }
    })
    assert usage.as_row() == {
        "upstream_input_tokens": 7,
        "upstream_cache_read_tokens": 3,
        "upstream_cache_creation_tokens": 2,
        "upstream_output_tokens": 5,
    }


# --- ledger completion ------------------------------------------------------

def test_update_proxy_usage_completes_a_streamed_row(tmp_path):
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    row_id = store.record_proxy_savings({
        "timestamp": time.time(),
        "agent": "claude",
        "provider": "anthropic",
        "session_id": None,
        "tokens_before": 1000,
        "tokens_after": 600,
        "content_types": {"log": 1},
        "passthrough": 0,
    })
    sniffer = UsageSniffer("anthropic")
    sniffer.feed(
        b'data: {"message":{"usage":{"input_tokens":10,'
        b'"cache_read_input_tokens":500,"cache_creation_input_tokens":20}}}\n'
    )
    store.update_proxy_usage(row_id, sniffer.usage.as_row())

    row = store.db.execute(
        "SELECT upstream_input_tokens AS i, upstream_cache_read_tokens AS r, "
        "upstream_cache_creation_tokens AS c FROM proxy_savings WHERE id=?",
        (row_id,),
    ).fetchone()
    assert (row["i"], row["r"], row["c"]) == (10, 500, 20)


def test_update_proxy_usage_is_a_noop_without_usage(tmp_path):
    """A stream that reported nothing must leave the columns NULL.

    Writing zeros would make "the provider never told us" indistinguishable
    from "there was no caching", and the second is a claim we cannot support.
    """
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    row_id = store.record_proxy_savings({
        "timestamp": time.time(), "agent": "claude", "provider": "anthropic",
        "session_id": None, "tokens_before": 10, "tokens_after": 10,
        "content_types": {}, "passthrough": 1,
    })
    store.update_proxy_usage(row_id, UsageSniffer("anthropic").usage.as_row())
    row = store.db.execute(
        "SELECT upstream_input_tokens AS i FROM proxy_savings WHERE id=?", (row_id,)
    ).fetchone()
    assert row["i"] is None
    store.update_proxy_usage(None, {"upstream_input_tokens": 5})  # must not raise


def test_migration_adds_cache_creation_column_to_old_ledger(tmp_path):
    import sqlite3

    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE proxy_savings(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp REAL, agent TEXT, provider TEXT, session_id TEXT, "
        "tokens_before INTEGER, tokens_after INTEGER, content_types TEXT, "
        "passthrough INTEGER DEFAULT 0, upstream_input_tokens INTEGER, "
        "upstream_cache_read_tokens INTEGER, upstream_output_tokens INTEGER)"
    )
    db.execute(
        "INSERT INTO proxy_savings(timestamp, agent, tokens_before, tokens_after) "
        "VALUES(?,?,?,?)", (time.time(), "claude", 100, 50))
    db.commit()
    db.close()

    store = SqliteStore(path, dim=16)
    cols = [r[1] for r in store.db.execute("PRAGMA table_info(proxy_savings)")]
    assert "upstream_cache_creation_tokens" in cols
    # The pre-existing row survives the migration.
    assert store.db.execute("SELECT COUNT(*) c FROM proxy_savings").fetchone()["c"] == 1


# --- net-of-cache accounting ------------------------------------------------

def _row(**kw):
    base = {
        "agent": "claude", "tokens_before": 0, "tokens_after": 0,
        "content_types": {}, "passthrough": 0,
    }
    base.update(kw)
    return base


def test_summary_ignores_rows_without_usage_for_cache_math():
    s = summarize_savings([
        _row(tokens_before=100, tokens_after=50),  # no usage reported
        _row(tokens_before=100, tokens_after=50,
             upstream_input_tokens=10, upstream_cache_read_tokens=90,
             upstream_cache_creation_tokens=0),
    ])
    assert s.requests == 2
    assert s.usage_requests == 1
    assert s.cache_read == 90


def test_net_saved_charges_cache_write_overhead():
    s = summarize_savings([
        _row(tokens_before=1000, tokens_after=600,
             upstream_input_tokens=100, upstream_cache_read_tokens=0,
             upstream_cache_creation_tokens=200),
    ])
    assert s.saved == 400
    overhead = 200 * (CACHE_WRITE_MULTIPLIER - CACHE_READ_MULTIPLIER)
    assert s.cache_overhead_units == overhead
    assert s.net_saved_units == 400 - overhead


def test_net_saved_can_go_negative_when_compression_busts_the_cache():
    """The case the whole feature exists to detect.

    A small gross saving alongside a large cache re-write is a net loss, and
    the gross number alone reports it as a win.
    """
    # Enough rows to clear MIN_REQUESTS; below it the report correctly
    # declines to state a rate at all.
    s = summarize_savings([
        _row(tokens_before=10_000, tokens_after=9_900,
             upstream_input_tokens=50, upstream_cache_read_tokens=0,
             upstream_cache_creation_tokens=9_900)
        for _ in range(30)
    ])
    assert s.saved == 3_000
    assert s.net_saved_units < 0
    report = "\n".join(format_report(s))
    assert "not paying for itself" in report


def test_billed_units_price_each_token_class():
    s = summarize_savings([
        _row(upstream_input_tokens=100, upstream_cache_read_tokens=1000,
             upstream_cache_creation_tokens=10),
    ])
    expected = 100 + 1000 * CACHE_READ_MULTIPLIER + 10 * CACHE_WRITE_MULTIPLIER
    assert s.billed_input_units == expected
    assert 89 < s.cache_hit_pct < 91  # 1000 of 1110 prompt tokens


def test_compressible_rate_excludes_passthrough_requests():
    """Blending passthroughs in reports compressor quality as coverage.

    The real ledger is 87.6% passthrough, which drags a genuine 7.3% rate on
    compressible traffic down to 0.77% overall. Both numbers are true; only
    one of them is about the compressor.
    """
    rows = [_row(tokens_before=1000, tokens_after=1000, passthrough=1) for _ in range(9)]
    rows.append(_row(tokens_before=1000, tokens_after=500, passthrough=0))
    s = summarize_savings(rows)
    assert s.compressible_pct == 50.0
    assert round(s.realized_pct, 1) == 5.0
    assert round(s.passthrough_pct) == 90


def test_report_states_when_cache_is_unmeasured():
    rows = [_row(tokens_before=100, tokens_after=90) for _ in range(30)]
    report = "\n".join(format_report(summarize_savings(rows)))
    assert "NET OF CACHE: unmeasured" in report


def test_report_shows_net_when_usage_present():
    rows = [
        _row(tokens_before=1000, tokens_after=500,
             upstream_input_tokens=100, upstream_cache_read_tokens=900,
             upstream_cache_creation_tokens=0)
        for _ in range(30)
    ]
    report = "\n".join(format_report(summarize_savings(rows)))
    assert "NET OF CACHE:" in report
    assert "unmeasured" not in report
    assert "not paying for itself" not in report


def test_load_savings_rows_tolerates_ledger_without_cache_column(tmp_path):
    """Read-only access cannot migrate, so it must degrade instead of failing."""
    import sqlite3

    from memor.compression_worth import load_savings_rows

    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE proxy_savings(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp REAL, agent TEXT, tokens_before INTEGER, tokens_after INTEGER, "
        "content_types TEXT, passthrough INTEGER DEFAULT 0, "
        "upstream_input_tokens INTEGER, upstream_cache_read_tokens INTEGER)"
    )
    db.execute(
        "INSERT INTO proxy_savings(timestamp, agent, tokens_before, tokens_after, "
        "content_types, passthrough) VALUES(?,?,?,?,?,?)",
        (time.time(), "claude", 100, 50, "{}", 0))
    db.commit()
    db.close()

    rows = load_savings_rows(path, days=30)
    assert len(rows) == 1
    assert "upstream_cache_creation_tokens" not in rows[0]
    assert summarize_savings(rows).saved == 50


def test_unreported_cache_writes_are_not_treated_as_zero():
    """A NULL write count is unknown, not zero.

    Rows written before the cache-write column existed report reads but no
    writes. Reading that as "zero writes" prices the one cost compression can
    actually add at nothing, and produces a confident net figure from data
    that cannot support one.
    """
    rows = [
        _row(tokens_before=1000, tokens_after=500,
             upstream_input_tokens=10, upstream_cache_read_tokens=900)
        for _ in range(30)
    ]
    s = summarize_savings(rows)
    assert s.has_usage
    assert not s.cache_writes_observed
    report = "\n".join(format_report(s))
    assert "floor of zero rather than a measurement" in report


def test_observed_zero_cache_writes_is_a_real_measurement():
    rows = [
        _row(tokens_before=1000, tokens_after=500,
             upstream_input_tokens=10, upstream_cache_read_tokens=900,
             upstream_cache_creation_tokens=0)
        for _ in range(30)
    ]
    s = summarize_savings(rows)
    assert s.cache_writes_observed
    assert "floor of zero" not in "\n".join(format_report(s))
