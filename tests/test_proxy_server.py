from __future__ import annotations
from fastapi.testclient import TestClient
from memor.proxy.server import create_proxy_app
from memor.embed.fake import FakeEmbedder


def test_health(tmp_path):
    e = FakeEmbedder(dim=16)
    app = create_proxy_app(str(tmp_path / "m.db"), embedder=e)
    c = TestClient(app)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_create_proxy_app_uses_db_dim_not_embedder_dim(tmp_path):
    """Existing DBs were often built with dim=256; proxy embedder may differ."""
    from memor.store.sqlite_store import SqliteStore

    db = str(tmp_path / "m.db")
    SqliteStore(db, dim=256)
    # FakeEmbedder dim=16 would previously SystemExit on open.
    e = FakeEmbedder(dim=16)
    app = create_proxy_app(db, embedder=e)
    c = TestClient(app)
    assert c.get("/health").status_code == 200


def test_messages_runs_pipeline_and_forwards(tmp_path, monkeypatch):
    from memor.proxy import server
    from memor.proxy.forward import ForwardResponse
    
    async def fake_forward(*, method, url, headers, content, stream):
        assert "api.anthropic.com" in url
        assert "x-api-key" in {k.lower() for k in headers}
        # return non-stream JSON response shape
        response_body = b'{"content":[{"type":"text","text":"hi"}],"usage":{"input_tokens":10,"output_tokens":2}}'
        return ForwardResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            content=response_body
        )
    
    monkeypatch.setattr(server, "forward_request", fake_forward)
    e = FakeEmbedder(dim=16)
    app = create_proxy_app(str(tmp_path / "m.db"), embedder=e)
    c = TestClient(app)
    body = {
        "model": "claude-sonnet-4-0",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
    }
    r = c.post("/v1/messages", json=body, headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"})
    assert r.status_code == 200


def test_messages_streaming(tmp_path, monkeypatch):
    from memor.proxy import server
    from memor.proxy.forward import StreamingForwardResponse
    
    async def fake_forward(*, method, url, headers, content, stream):
        assert "api.anthropic.com" in url
        assert stream is True
        # Return mock streaming response
        class MockStreamingResponse:
            async def __aenter__(self):
                self.status_code = 200
                self.headers = {"content-type": "text/event-stream"}
                return self
            
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass
            
            async def aiter_bytes(self):
                yield b'data: {"type":"content_block_start"}\n\n'
                yield b'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n\n'
                yield b'data: {"type":"message_stop"}\n\n'
        
        return MockStreamingResponse()
    
    monkeypatch.setattr(server, "forward_request", fake_forward)
    e = FakeEmbedder(dim=16)
    app = create_proxy_app(str(tmp_path / "m.db"), embedder=e)
    c = TestClient(app)
    body = {
        "model": "claude-sonnet-4-0",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
        "stream": True,
    }
    r = c.post("/v1/messages", json=body, headers={"x-api-key": "test-key"})
    assert r.status_code == 200
    # Verify we got streaming content
    content = r.content
    assert b"content_block_start" in content
