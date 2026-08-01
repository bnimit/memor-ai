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
            "usage": {"input_tokens": 10, "output_tokens": 2},
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
