"""Ledger rows written before the proxy could identify its caller.

A proxy sees what a request carries, never who dialled it. Before path
prefixes and the x-agent header existed every row landed under "unknown",
and the dashboard rendered them as an agent saving 75.9% off twenty requests.

Two guards: relabel the rows where the config makes the agent unambiguous, and
stop reporting a percentage at all when the sample cannot support one.
"""
from __future__ import annotations

import time

import pytest

from memor.store.sqlite_store import MIN_AGENT_SAMPLE, SqliteStore


def _row(store, *, agent, provider, before, after, passthrough=0, ago=60):
    store.db.execute(
        "INSERT INTO proxy_savings(timestamp, agent, provider, session_id, "
        "tokens_before, tokens_after, content_types, passthrough) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (time.time() - ago, agent, provider, None, before, after, "{}", passthrough))
    store.db.commit()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "s.db")


@pytest.fixture
def single_anthropic(monkeypatch):
    """One enabled agent on the anthropic protocol: attribution is unambiguous."""
    import memor.proxy.upstream as upstream
    monkeypatch.setattr(upstream, "load_config", lambda: {
        "proxy_agents": {"claude": True, "goose": True},
        "proxy_upstreams": {
            "claude": {"protocol": "anthropic", "base_url": "https://a"},
            "goose": {"protocol": "openai", "base_url": "https://b"},
        },
    })


@pytest.fixture
def two_anthropic(monkeypatch):
    """Two enabled agents share the protocol: nothing can be inferred."""
    import memor.proxy.upstream as upstream
    monkeypatch.setattr(upstream, "load_config", lambda: {
        "proxy_agents": {"claude": True, "cline": True},
        "proxy_upstreams": {
            "claude": {"protocol": "anthropic", "base_url": "https://a"},
            "cline": {"protocol": "anthropic", "base_url": "https://b"},
        },
    })


def _agents(path):
    return {r["agent"]: r for r in SqliteStore(path, dim=16).get_proxy_savings_by_agent(30)}


# --- the backfill -------------------------------------------------------------


def test_unambiguous_rows_are_relabelled(db_path, single_anthropic):
    store = SqliteStore(db_path, dim=16)
    _row(store, agent="unknown", provider="anthropic", before=1000, after=250)
    store.db.close()

    agents = _agents(db_path)
    assert "unknown" not in agents
    assert agents["claude"]["tokens_before"] == 1000


def test_ambiguous_rows_are_left_alone(db_path, two_anthropic):
    """Two agents on one protocol: "unknown" is the honest label."""
    store = SqliteStore(db_path, dim=16)
    _row(store, agent="unknown", provider="anthropic", before=1000, after=250)
    store.db.close()

    assert "unknown" in _agents(db_path)


def test_rows_for_an_unconfigured_protocol_are_left_alone(db_path, single_anthropic):
    store = SqliteStore(db_path, dim=16)
    _row(store, agent="unknown", provider="mystery", before=500, after=100)
    store.db.close()

    assert "unknown" in _agents(db_path)


def test_already_attributed_rows_are_untouched(db_path, single_anthropic):
    store = SqliteStore(db_path, dim=16)
    _row(store, agent="goose", provider="openai", before=400, after=300)
    store.db.close()

    agents = _agents(db_path)
    assert agents["goose"]["tokens_before"] == 400


def test_backfill_merges_into_the_existing_agent_total(db_path, single_anthropic):
    store = SqliteStore(db_path, dim=16)
    _row(store, agent="claude", provider="anthropic", before=1000, after=900)
    _row(store, agent="unknown", provider="anthropic", before=1000, after=100)
    store.db.close()

    agents = _agents(db_path)
    assert list(agents) == ["claude"]
    assert agents["claude"]["tokens_before"] == 2000
    assert agents["claude"]["tokens_after"] == 1000


def test_backfill_is_idempotent(db_path, single_anthropic):
    store = SqliteStore(db_path, dim=16)
    _row(store, agent="unknown", provider="anthropic", before=1000, after=250)
    store.db.close()

    for _ in range(3):
        agents = _agents(db_path)
    assert agents["claude"]["tokens_before"] == 1000


def test_an_empty_ledger_is_not_a_problem(db_path, single_anthropic):
    assert _agents(db_path) == {}


# --- the sample floor ---------------------------------------------------------


def test_a_small_sample_is_flagged(db_path, single_anthropic):
    store = SqliteStore(db_path, dim=16)
    for _ in range(MIN_AGENT_SAMPLE - 1):
        _row(store, agent="claude", provider="anthropic", before=100, after=25)
    store.db.close()

    assert _agents(db_path)["claude"]["low_sample"] is True


def test_a_sufficient_sample_is_not_flagged(db_path, single_anthropic):
    store = SqliteStore(db_path, dim=16)
    for _ in range(MIN_AGENT_SAMPLE):
        _row(store, agent="claude", provider="anthropic", before=100, after=25)
    store.db.close()

    row = _agents(db_path)["claude"]
    assert row["low_sample"] is False
    assert row["pct_saved"] == 75.0


def test_passthrough_requests_do_not_count_toward_the_sample(db_path, single_anthropic):
    """500 untouched requests are not evidence about the compression rate."""
    store = SqliteStore(db_path, dim=16)
    for _ in range(500):
        _row(store, agent="claude", provider="anthropic", before=100, after=100,
             passthrough=1)
    _row(store, agent="claude", provider="anthropic", before=100, after=25)
    store.db.close()

    assert _agents(db_path)["claude"]["low_sample"] is True


def test_the_totals_survive_the_flag(db_path, single_anthropic):
    """Flagging withholds the rate, it does not hide the tokens."""
    store = SqliteStore(db_path, dim=16)
    _row(store, agent="claude", provider="anthropic", before=64587, after=15541)
    store.db.close()

    row = _agents(db_path)["claude"]
    assert row["low_sample"] is True
    assert row["tokens_before"] == 64587
    assert row["tokens_after"] == 15541
