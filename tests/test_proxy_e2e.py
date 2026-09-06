"""End-to-end proxy tests over real HTTP.

These drive the proxy app through an actual client and route the upstream call
to a local ASGI app via httpx's ASGITransport, so `forward_request` and the
header/body handling around it run for real.
"""
from __future__ import annotations

import gzip
import json
import time

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from memor.embed.fake import FakeEmbedder
from memor.proxy import forward as forward_mod
from memor.proxy.forward import sanitize_request_headers, sanitize_response_headers
from memor.proxy.server import create_proxy_app


def _upstream_app() -> FastAPI:
    """Echo upstream that reports the exact framing it received."""
    app = FastAPI()

    @app.post("/v1/messages")
    async def messages(request: Request):
        raw = await request.body()
        payload = {
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 2,
                "cache_read_input_tokens": 500,
                "cache_creation_input_tokens": 40,
            },
            "echo": {
                "content_length": request.headers.get("content-length"),
                "actual_length": len(raw),
                "transfer_encoding": request.headers.get("transfer-encoding"),
                "content_encoding": request.headers.get("content-encoding"),
                "connection": request.headers.get("connection"),
                "api_key": request.headers.get("x-api-key"),
                "body": json.loads(raw),
            },
        }
        if request.headers.get("x-test-stream") == "1":
            async def gen():
                yield b'event: message_start\ndata: {"type":"message_start"}\n\n'
                yield b"data: " + json.dumps(payload).encode() + b"\n\n"
            return StreamingResponse(
                gen(),
                media_type="text/event-stream",
                headers={"transfer-encoding": "chunked", "connection": "keep-alive"},
            )
        body = json.dumps(payload).encode()
        if request.headers.get("x-test-gzip") == "1":
            packed = gzip.compress(body)
            return Response(
                content=packed,
                media_type="application/json",
                headers={
                    "content-encoding": "gzip",
                    "content-length": str(len(packed)),
                },
            )
        return Response(content=body, media_type="application/json")

    return app


@pytest.fixture
def proxy_client(monkeypatch, tmp_path):
    """Proxy TestClient whose upstream calls land on a local ASGI app."""
    transport = httpx.ASGITransport(app=_upstream_app())
    real_async_client = httpx.AsyncClient

    def bound_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(forward_mod.httpx, "AsyncClient", bound_client)

    db_path = str(tmp_path / "m.db")
    app = create_proxy_app(db_path, embedder=FakeEmbedder(dim=16))
    with TestClient(app) as client:
        yield client, db_path


def _compressible_body() -> dict:
    log = "\n".join([f"INFO noise {i}" for i in range(120)] + ["ERROR boom"])
    return {
        "model": "claude-sonnet-4-0",
        "max_tokens": 64,
        "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": log},
            ]},
        ],
    }


def test_forwarded_body_framing_matches_recompressed_payload(proxy_client):
    client, _ = proxy_client
    body = _compressible_body()
    raw = json.dumps(body).encode()

    r = client.post(
        "/v1/messages",
        content=raw,
        headers={
            "content-type": "application/json",
            "content-length": str(len(raw)),
            "connection": "close",
            "x-api-key": "test-key",
            "x-agent": "claude",
        },
    )
    assert r.status_code == 200
    echo = r.json()["echo"]

    # httpx recomputed Content-Length for the rewritten body.
    assert echo["content_length"] == str(echo["actual_length"])
    assert echo["content_length"] != str(len(raw))
    assert echo["transfer_encoding"] is None
    assert echo["content_encoding"] is None
    # The client's connection header did not survive; httpx set its own.
    assert echo["connection"] != "close"
    # Auth headers still survive the sanitising pass.
    assert echo["api_key"] == "test-key"
    # And the upstream really did receive the compressed payload.
    forwarded = echo["body"]["messages"][0]["content"][0]["content"]
    assert forwarded.startswith("[memor:ccr:")


def test_ledger_row_is_readable_by_savings_ledger_endpoint(proxy_client):
    client, db_path = proxy_client
    r = client.post(
        "/v1/messages",
        json=_compressible_body(),
        headers={"x-api-key": "test-key", "x-agent": "claude"},
    )
    assert r.status_code == 200

    from memor.dashboard.server import create_app

    dash = TestClient(create_app(db_path))
    data = dash.get("/api/savings-ledger?days=30").json()

    assert data["summary"]["tokens_before"] > data["summary"]["tokens_after"] > 0
    assert data["content_types"] == [{"content_type": "log", "count": 1}]
    assert len(data["per_day"]) == 1


def test_gzipped_upstream_response_is_decoded_and_reframed(proxy_client):
    client, _ = proxy_client
    r = client.post(
        "/v1/messages",
        json=_compressible_body(),
        headers={"x-api-key": "test-key", "x-test-gzip": "1", "x-agent": "claude"},
    )
    assert r.status_code == 200
    # Body reached the client decoded, so the upstream framing headers are gone.
    assert "content-encoding" not in {k.lower() for k in r.headers}
    assert r.json()["usage"]["input_tokens"] == 10
    assert int(r.headers["content-length"]) == len(r.content)


def _ledger_usage(db_path: str) -> dict:
    import sqlite3

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT upstream_input_tokens AS i, upstream_cache_read_tokens AS r, "
        "upstream_cache_creation_tokens AS c, upstream_output_tokens AS o "
        "FROM proxy_savings ORDER BY id DESC LIMIT 1"
    ).fetchone()
    db.close()
    return dict(row)


def test_streaming_request_records_upstream_usage(proxy_client):
    """The regression that left every usage column NULL on real traffic.

    Agents stream, and the streaming branch wrote its ledger row before the
    first byte was forwarded, so usage -- which the provider only reports
    inside the stream -- was hardcoded to None. Without these numbers there is
    no way to tell whether compression saved money or merely converted cheap
    cache reads into expensive cache writes.
    """
    client, db_path = proxy_client
    body = _compressible_body()
    body["stream"] = True
    r = client.post(
        "/v1/messages",
        json=body,
        headers={"x-api-key": "test-key", "x-test-stream": "1", "x-agent": "claude"},
    )
    assert r.status_code == 200
    assert b"message_start" in r.content

    usage = _ledger_usage(db_path)
    assert usage["i"] == 10
    assert usage["r"] == 500
    assert usage["c"] == 40
    assert usage["o"] == 2


def test_non_streaming_request_records_cache_counters(proxy_client):
    client, db_path = proxy_client
    r = client.post(
        "/v1/messages",
        json=_compressible_body(),
        headers={"x-api-key": "test-key", "x-agent": "claude"},
    )
    assert r.status_code == 200
    usage = _ledger_usage(db_path)
    assert (usage["i"], usage["r"], usage["c"]) == (10, 500, 40)


def test_streamed_bytes_reach_the_client_unaltered(proxy_client):
    """Sniffing usage must not disturb the stream it observes."""
    client, _ = proxy_client
    body = _compressible_body()
    body["stream"] = True
    r = client.post(
        "/v1/messages",
        json=body,
        headers={"x-api-key": "test-key", "x-test-stream": "1", "x-agent": "claude"},
    )
    text = r.content.decode()
    assert text.startswith("event: message_start\ndata: ")
    assert text.count("data: ") == 2
    assert text.endswith("\n\n")


def test_streaming_response_drops_hop_by_hop_headers(proxy_client):
    client, _ = proxy_client
    body = _compressible_body()
    body["stream"] = True
    r = client.post(
        "/v1/messages",
        json=body,
        headers={"x-api-key": "test-key", "x-test-stream": "1", "x-agent": "claude"},
    )
    assert r.status_code == 200
    assert b"message_start" in r.content
    lowered = {k.lower() for k in r.headers}
    assert "connection" not in lowered
    assert "content-encoding" not in lowered


def test_sanitize_request_headers_drops_client_framing():
    cleaned = sanitize_request_headers({
        "Host": "127.0.0.1:8421",
        "Content-Length": "1234",
        "Transfer-Encoding": "chunked",
        "Content-Encoding": "gzip",
        "Connection": "keep-alive",
        "X-Api-Key": "secret",
        "anthropic-version": "2023-06-01",
    })
    assert cleaned == {"X-Api-Key": "secret", "anthropic-version": "2023-06-01"}


def test_sanitize_response_headers_keeps_content_type():
    cleaned = sanitize_response_headers({
        "Content-Type": "application/json",
        "Content-Length": "99",
        "Content-Encoding": "gzip",
        "Connection": "keep-alive",
        "request-id": "abc",
    })
    assert cleaned == {"Content-Type": "application/json", "request-id": "abc"}


def _ledger(db_path: str) -> list[dict]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM proxy_savings ORDER BY id")]
    finally:
        conn.close()


def test_a_compressed_streamed_request_records_what_it_was_billed(proxy_client):
    """The measurement chain, end to end, on the case that matters most.

    Gross savings are computed by memor from its own compressor, so they cannot
    say whether the bill moved. Only the provider's usage counters can, and on
    a compressed request they are the difference between a saving and a
    cache-busting loss.

    Every unit in this path was already tested -- the sniffer, the ledger
    update, the net-of-cache arithmetic -- and the whole still produced a
    ledger where 0 of 671 compressed proxy rows carried usage. Unit tests
    cannot see that; only driving the real endpoint can.
    """
    client, db_path = proxy_client
    body = _compressible_body()
    body["stream"] = True          # the branch agents actually take
    r = client.post(
        "/v1/messages",
        json=body,
        headers={"x-test-stream": "1", "x-agent": "claude",
                 "x-session-id": "s-net"},
    )
    assert r.status_code == 200
    assert b"message_start" in r.content

    rows = [x for x in _ledger(db_path) if not x["passthrough"]]
    assert rows, "compression happened but nothing reached the ledger"
    row = rows[-1]

    # Gross: memor's own view of what it removed.
    assert row["tokens_before"] > row["tokens_after"]
    # Net: the provider's view of what it charged for. Without these the
    # dashboard can only ever report a number nobody can bill against.
    assert row["upstream_input_tokens"] == 10
    assert row["upstream_cache_read_tokens"] == 500
    assert row["upstream_cache_creation_tokens"] == 40


def test_usage_is_captured_on_compressed_and_passthrough_alike(proxy_client):
    """Usage capture must not correlate with whether compression fired.

    On the real ledger it did, and starkly: 30.7% of passthrough rows carried
    usage against 0.1% of compressed ones. That asymmetry is what makes the
    net-of-cache figure unavailable exactly where it is needed, so it is
    asserted rather than assumed.
    """
    client, db_path = proxy_client
    # Too small to compress: this is the passthrough arm.
    tiny = {"model": "claude-sonnet-4-0", "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}]}
    for body in (tiny, _compressible_body()):
        body = dict(body, stream=True)
        r = client.post(
            "/v1/messages",
            json=body,
            headers={"x-test-stream": "1", "x-agent": "claude"},
        )
        assert r.status_code == 200
        assert b"message_start" in r.content

    rows = _ledger(db_path)
    assert {r["passthrough"] for r in rows} == {0, 1}, "need both arms"
    for r in rows:
        assert r["upstream_input_tokens"] is not None, (
            f"passthrough={r['passthrough']} row has no usage")
