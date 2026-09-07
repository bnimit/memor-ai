"""The dashboard must show who is *reading* memory, not who is configured to.

The Agents chip read `proxy_agents`, which is configuration. Cursor and jcode
sat there looking healthy for weeks after they stopped recalling, and both were
in fact invisible to it anyway: they read through hooks and MCP rather than the
proxy, so their 459 recalls never touched that config at all.
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from memor.liveness import MIN_SAMPLE, STALE_DAYS, UNATTRIBUTED
from memor.store.sqlite_store import SqliteStore

_DAY = 86_400


def _recall(store: SqliteStore, *, agent: str, at: float, hits: int) -> None:
    store.db.execute(
        "INSERT INTO recall_log(timestamp, project, query_preview, hits_count,"
        " top_score, tokens_injected, latency_ms, status, session_id, agent)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (at, "demo", "q", hits, 0.5 if hits else 0.0, 10, 1.0,
         "ok" if hits else "no_hits", "s1", agent),
    )
    store.db.commit()


def _client(tmp_path) -> tuple[TestClient, SqliteStore]:
    db_path = str(tmp_path / "m.db")
    store = SqliteStore(db_path, dim=16)
    from memor.dashboard.server import create_app

    return TestClient(create_app(db_path)), store


def test_health_reports_each_reader(tmp_path) -> None:
    client, store = _client(tmp_path)
    now = time.time()
    for _ in range(MIN_SAMPLE):
        _recall(store, agent="claude", at=now - _DAY, hits=1)
    _recall(store, agent="cursor", at=now - (STALE_DAYS + 5) * _DAY, hits=1)

    readers = client.get("/api/health").json()["readers"]

    by_agent = {r["agent"]: r for r in readers}
    assert by_agent["claude"]["status"] == "live"
    assert by_agent["cursor"]["status"] == "stale"


def test_health_reports_readers_the_proxy_config_never_knew_about(tmp_path) -> None:
    """jcode and Cursor read through hooks and MCP, never the proxy.

    A chip fed only by proxy_agents cannot see them, which is half the reason
    their silence went unnoticed.
    """
    client, store = _client(tmp_path)
    now = time.time()
    _recall(store, agent="jcode", at=now - _DAY, hits=1)

    readers = client.get("/api/health").json()["readers"]

    assert [r["agent"] for r in readers] == ["jcode"]


def test_unattributed_recalls_are_not_reported_as_a_broken_agent(tmp_path) -> None:
    """"unknown" is a bucket for headerless requests, not a tool to go fix."""
    client, store = _client(tmp_path)
    now = time.time()
    _recall(store, agent=UNATTRIBUTED, at=now - (STALE_DAYS + 5) * _DAY, hits=0)

    readers = client.get("/api/health").json()["readers"]

    (entry,) = readers
    assert entry["agent"] == UNATTRIBUTED
    # Still surfaced, but never as "stopped reading; check the integration".
    assert not any("check the integration" in n for n in entry["notes"])
    assert any("not an agent" in n for n in entry["notes"])


def test_health_still_reports_the_write_path(tmp_path) -> None:
    """Reader liveness is added alongside compression_paths, not instead."""
    client, _store = _client(tmp_path)

    health = client.get("/api/health").json()

    assert "compression_paths" in health
    assert "readers" in health


def test_health_on_an_unread_store_returns_an_empty_reader_list(tmp_path) -> None:
    client, _store = _client(tmp_path)

    assert client.get("/api/health").json()["readers"] == []
