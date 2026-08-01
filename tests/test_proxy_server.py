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


def test_messages_runs_pipeline_and_forwards(tmp_path, monkeypatch):
    from memor.proxy import server
    async def fake_forward(*, method, url, headers, content, stream):
        assert "api.anthropic.com" in url
        assert "x-api-key" in {k.lower() for k in headers}
        # return non-stream JSON response shape
        class R:
            status_code = 200
            headers = {"content-type": "application/json"}
            async def aiter_bytes(self):
                yield b'{"content":[{"type":"text","text":"hi"}],"usage":{"input_tokens":10,"output_tokens":2}}'
            async def aread(self):
                return b'{"content":[{"type":"text","text":"hi"}],"usage":{"input_tokens":10,"output_tokens":2}}'
        return R()
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
