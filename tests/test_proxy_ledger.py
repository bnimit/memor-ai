import time
from memor.store.sqlite_store import SqliteStore

def test_record_and_summary(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.record_proxy_savings({
        "timestamp": time.time(),
        "agent": "claude",
        "provider": "anthropic",
        "session_id": "s1",
        "tokens_before": 1000,
        "tokens_after": 400,
        "content_types": {"log": 1},
        "passthrough": 0,
    })
    summary = s.get_proxy_savings_summary(days=30)
    assert summary["tokens_before"] == 1000
    assert summary["tokens_after"] == 400
    assert summary["pct_saved"] == 60.0

def test_ccr_put_get_evict(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.ccr_put("b1", "FULL TEXT", "log", created_at=1.0)
    assert s.ccr_get("b1") == "FULL TEXT"
    n = s.ccr_evict(ttl_seconds=0, max_bytes=1)  # everything expired / over cap
    assert n >= 1
    assert s.ccr_get("b1") is None


def _row(before, after, passthrough=0, ts=None):
    return {
        "timestamp": ts if ts is not None else time.time(),
        "agent": "claude", "provider": "anthropic", "session_id": "s1",
        "tokens_before": before, "tokens_after": after,
        "content_types": {"log": 1}, "passthrough": passthrough,
    }


def test_passthroughs_do_not_dilute_the_headline_rate(tmp_path):
    """A forwarded request had nothing to compress; it is not a failed one.

    Counting passthroughs in the denominator makes the hero percentage track
    traffic mix instead of the compressor. The real ledger is ~86% passthrough,
    which turned a genuine 83.7% rate into 8.1%. The per-day series always
    excluded them, so the headline and the curve under it disagreed.
    """
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.record_proxy_savings(_row(1000, 200))
    for _ in range(20):
        s.record_proxy_savings(_row(5000, 5000, passthrough=1))

    summary = s.get_proxy_savings_summary(days=30)
    assert summary["pct_saved"] == 80.0
    assert summary["tokens_saved"] == 800
    # Low coverage stays visible instead of being disguised as weak compression.
    assert summary["coverage_pct"] < 5.0
    assert summary["requests"] == 21 and summary["compressed_requests"] == 1

    # The two figures on the same panel must come from the same population.
    series = s.get_proxy_savings_series(days=30)
    assert series[-1]["cumulative_saved"] == summary["tokens_saved"]


def test_lifetime_total_does_not_shrink_as_traffic_ages_out(tmp_path):
    """The headline fell 2.5M -> 256k with no code change and no data loss.

    Both figures were a rolling 30-day window labelled "cumulative", so old
    savings silently aged out and a quiet month read as a regression. The
    lifetime total is monotonic and is what the label promised.
    """
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    old = time.time() - 60 * 86400
    s.record_proxy_savings(_row(1_000_000, 100_000, ts=old))
    s.record_proxy_savings(_row(10_000, 1_000))

    assert s.get_proxy_savings_summary(days=30)["tokens_saved"] == 9_000
    assert s.get_proxy_savings_summary(days=None)["tokens_saved"] == 909_000


def test_hook_and_proxy_savings_are_split(tmp_path):
    """The two paths do not carry the same caveat, so the hero must not blend.

    A hook rewrite lands before the payload enters the transcript, so there is
    no cached prefix to invalidate. A proxy rewrite may hit one. format_report
    has always kept them apart; the dashboard ledger endpoint did not, and on
    the real store every recent row is provider='hook'.
    """
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    hook = _row(1000, 100)
    hook["provider"] = "hook"
    s.record_proxy_savings(hook)
    s.record_proxy_savings(_row(500, 300))

    summary = s.get_proxy_savings_summary(days=None)
    assert summary["hook_saved"] == 900
    assert summary["proxy_saved"] == 200
    assert summary["hook_saved"] + summary["proxy_saved"] == summary["tokens_saved"]


def test_ledger_key_makes_hook_retries_a_noop(tmp_path):
    """PostToolUse can fire several times for one tool result."""
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    row = _row(1000, 100)
    row["provider"] = "hook"
    row["ledger_key"] = "hook:same-event"
    first = s.record_proxy_savings(row)
    second = s.record_proxy_savings(row)
    assert first == second
    assert s.db.execute("SELECT COUNT(*) AS c FROM proxy_savings").fetchone()["c"] == 1
    assert s.get_proxy_savings_summary(days=None)["tokens_saved"] == 900


def test_existing_hook_duplicates_are_collapsed_on_open(tmp_path):
    """Rows written before ledger_key inflated hook savings ~2x on a real store."""
    path = str(tmp_path / "m.db")
    # Build a pre-migration ledger by writing raw SQL, then open SqliteStore so
    # the one-shot dedupe migration runs.
    s0 = SqliteStore(path, dim=16)
    ts = time.time()
    for _ in range(4):
        s0.db.execute(
            "INSERT INTO proxy_savings(timestamp, agent, provider, session_id, "
            "tokens_before, tokens_after, content_types, passthrough) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (ts, "claude", "hook", "s1", 1000, 100, "{}", 0),
        )
    # A second later is a different event and must survive.
    s0.db.execute(
        "INSERT INTO proxy_savings(timestamp, agent, provider, session_id, "
        "tokens_before, tokens_after, content_types, passthrough) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (ts + 2, "claude", "hook", "s1", 1000, 100, "{}", 0),
    )
    s0.db.execute("DELETE FROM meta WHERE key='hook_ledger_deduped'")
    s0.db.commit()
    s0.db.close()

    s = SqliteStore(path, dim=16)
    n = s.db.execute(
        "SELECT COUNT(*) AS c FROM proxy_savings WHERE provider='hook'"
    ).fetchone()["c"]
    assert n == 2
    assert s.get_proxy_savings_summary(days=None)["hook_saved"] == 1800
