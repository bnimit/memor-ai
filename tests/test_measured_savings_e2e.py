"""End-to-end: does the measured figure match what the provider actually billed?

Every other test here checks a component. This one drives real requests through
the real proxy route against an upstream that **bills what it is actually
sent**, then recomputes the answer independently from the ledger's own billed
columns and compares. That is the difference between "the code ran" and "the
number is right".

The earlier version of this check used an upstream that reported the same usage
whatever it received. The measured figure came out 0.0%, which looked like a
pass and proved nothing: a report that always printed zero would have satisfied
it. An upstream that prices its input is what makes the loop able to fail.
"""
from __future__ import annotations

import sqlite3
import statistics
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from memor.compression_worth import OUTPUT_MULTIPLIER, report
from memor.embed.fake import FakeEmbedder
from memor.store.sqlite_store import SqliteStore
from memor.tokencount import count_tokens

REQUESTS = 140


def _sse(input_tokens: int, output_tokens: int) -> bytes:
    return (
        f'event: message_start\ndata: {{"type":"message_start","message":'
        f'{{"usage":{{"input_tokens":{input_tokens},"cache_read_input_tokens":0,'
        f'"cache_creation_input_tokens":0,"output_tokens":1}}}}}}\n\n'
        f'event: message_delta\ndata: {{"type":"message_delta","usage":'
        f'{{"output_tokens":{output_tokens}}}}}\n\n'
    ).encode()


class _Response:
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def aiter_bytes(self):
        yield self._body

    async def aread(self) -> bytes:
        return self._body


class _Ctx:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def __aenter__(self):
        return _Response(self._body)

    async def __aexit__(self, *_a):
        return False


def _run_traffic(db: str, embedder, output_for) -> None:
    """Drive REQUESTS real requests through the proxy route.

    ``output_for`` receives the token count actually forwarded and returns how
    many output tokens the provider bills, which is how a test can express
    "compression made the model write more".
    """
    async def forward(*_a, **kw):
        sent = count_tokens(kw["content"].decode())
        body = _sse(sent, output_for(sent))
        return _Ctx(body) if kw.get("stream") else _Response(body)

    import memor.proxy.server as ps

    with mock.patch.object(ps, "forward_request", forward), \
         mock.patch.object(ps, "resolve_upstream_url", lambda _a, _p: "https://x"):
        client = TestClient(ps.create_proxy_app(db_path=db, embedder=embedder))
        for i in range(REQUESTS):
            log = "\n".join(
                f"2026-09-07 12:00:{j:02d} INFO run={i} worker={j} status=ok"
                for j in range(200)
            )
            client.post("/v1/messages", json={
                "model": "claude-3", "stream": True, "messages": [
                    {"role": "user", "content": f"run {i}"},
                    {"role": "assistant", "content": [{
                        "type": "tool_use", "id": "t", "name": "Bash",
                        "input": {"command": "pytest"}}]},
                    {"role": "user", "content": [{
                        "type": "tool_result", "tool_use_id": "t",
                        "content": log}]},
                ],
            })


def _ground_truth(db: str) -> tuple[float, int, int]:
    """Recompute the answer from the billed columns, independently of the report."""
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT experiment_arm, upstream_input_tokens AS ui,"
        " upstream_output_tokens AS uo FROM proxy_savings"
        " WHERE upstream_input_tokens IS NOT NULL")]
    con.close()

    def cost(arm):
        vals = [r["ui"] + OUTPUT_MULTIPLIER * r["uo"]
                for r in rows if r["experiment_arm"] == arm]
        return statistics.mean(vals), len(vals)

    treated, n_t = cost("compress")
    control, n_c = cost("holdout")
    return (control - treated) / control * 100, n_t, n_c


@pytest.fixture
def holdout(monkeypatch):
    monkeypatch.setenv("MEMOR_HOLDOUT_FRACTION", "0.5")


def test_the_measured_figure_matches_what_the_provider_billed(tmp_path, holdout):
    """The acceptance check: report versus an independent recomputation."""
    db = str(tmp_path / "m.db")
    SqliteStore(db, dim=256)

    _run_traffic(db, FakeEmbedder(dim=256), output_for=lambda _sent: 200)

    truth, n_treated, n_control = _ground_truth(db)
    text = "\n".join(report(db, days=1))

    # Both arms must clear the 57-per-arm floor, or the report withholds.
    assert n_treated >= 57 and n_control >= 57
    # Compression really does save here, so the loop can distinguish outcomes.
    assert truth > 50
    assert f"cost {truth:.1f}% less than held-out ones" in text


def test_output_expansion_flips_the_measured_verdict(tmp_path, holdout):
    """The same code must report a loss when compression causes one.

    A compressed prompt is short, so this upstream has the model write 3,000
    tokens instead of 200 — arXiv:2603.23527's failure mode. Input falls and
    total cost rises; a report that only counted input would still claim a win.
    """
    db = str(tmp_path / "m.db")
    SqliteStore(db, dim=256)

    _run_traffic(
        db, FakeEmbedder(dim=256),
        output_for=lambda sent: 3000 if sent < 3000 else 200,
    )

    truth, _n_t, _n_c = _ground_truth(db)
    text = "\n".join(report(db, days=1))

    assert truth < 0, "the scenario must actually cost money"
    assert f"cost {abs(truth):.1f}% MORE than held-out ones" in text
    assert "VERDICT: compression is costing money" in text


def test_controls_reach_the_provider_uncompressed(tmp_path, holdout):
    """A control that was quietly compressed would bias the whole comparison."""
    db = str(tmp_path / "m.db")
    SqliteStore(db, dim=256)

    _run_traffic(db, FakeEmbedder(dim=256), output_for=lambda _sent: 200)

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT experiment_arm, tokens_before, tokens_after,"
        " upstream_input_tokens AS ui FROM proxy_savings")]
    con.close()

    controls = [r for r in rows if r["experiment_arm"] == "holdout"]
    treated = [r for r in rows if r["experiment_arm"] == "compress"]

    assert controls and treated
    # Held out: nothing removed, and the provider was billed the full payload.
    assert all(r["tokens_before"] == r["tokens_after"] for r in controls)
    assert all(r["tokens_after"] < r["tokens_before"] for r in treated)
    assert (statistics.mean(r["ui"] for r in controls)
            > statistics.mean(r["ui"] for r in treated))
