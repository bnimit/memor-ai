"""Tests for runtime fail-open proxy shim."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from memor.embed.fake import FakeEmbedder
from memor.proxy import shim
from memor.proxy.forward import ForwardResponse
from memor.proxy.server import create_proxy_app
from memor.store.sqlite_store import SqliteStore


@pytest.fixture(autouse=True)
def reset_compressor_state():
    shim.compressor_state.mode = "compress"
    shim.compressor_state.compressor_ready = True
    yield
    shim.compressor_state.mode = "compress"
    shim.compressor_state.compressor_ready = True


def _anthropic_body() -> dict:
    return {
        "model": "claude-sonnet-4-0",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
    }


def test_pipeline_failure_forwards_original_body_and_records_passthrough(tmp_path, monkeypatch):
    from memor.proxy import server

    original = _anthropic_body()
    forwarded: dict[str, bytes] = {}

    def boom(provider, body, store):
        raise RuntimeError("compressor down")

    async def fake_forward(*, method, url, headers, content, stream):
        forwarded["content"] = content
        return ForwardResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"content":[{"type":"text","text":"hi"}],"usage":{"input_tokens":10,"output_tokens":2}}',
        )

    monkeypatch.setattr(shim, "run_pipeline", boom)
    monkeypatch.setattr(server, "forward_request", fake_forward)

    db_path = str(tmp_path / "m.db")
    app = create_proxy_app(db_path, embedder=FakeEmbedder(dim=16))
    client = TestClient(app)

    r = client.post(
        "/v1/messages",
        json=original,
        headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01", "x-agent": "claude"},
    )
    assert r.status_code == 200
    assert json.loads(forwarded["content"].decode()) == original

    store = SqliteStore(db_path, dim=16)
    row = store.db.execute("SELECT * FROM proxy_savings ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["passthrough"] == 1
    assert row["tokens_before"] == row["tokens_after"]


def test_health_reflects_passthrough_after_compressor_failure(tmp_path, monkeypatch):
    from memor.proxy import server

    def boom(provider, body, store):
        raise RuntimeError("compressor down")

    async def fake_forward(*, method, url, headers, content, stream):
        return ForwardResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"content":[{"type":"text","text":"hi"}],"usage":{"input_tokens":10,"output_tokens":2}}',
        )

    monkeypatch.setattr(shim, "run_pipeline", boom)
    monkeypatch.setattr(server, "forward_request", fake_forward)

    db_path = str(tmp_path / "m.db")
    app = create_proxy_app(db_path, embedder=FakeEmbedder(dim=16))
    client = TestClient(app)

    health = client.get("/health").json()
    assert health["ok"] is True
    assert health["mode"] == "compress"
    assert health["compressor_ready"] is True

    client.post(
        "/v1/messages",
        json=_anthropic_body(),
        headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01", "x-agent": "claude"},
    )

    health = client.get("/health").json()
    assert health["ok"] is True
    assert health["mode"] == "passthrough"
    assert health["compressor_ready"] is False
