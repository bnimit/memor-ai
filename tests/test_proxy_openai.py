from __future__ import annotations
from fastapi.testclient import TestClient
from memor.proxy.server import create_proxy_app
from memor.embed.fake import FakeEmbedder


def test_chat_completions_runs_pipeline_and_forwards(tmp_path, monkeypatch):
    """Test OpenAI chat completions endpoint with tool messages."""
    from memor.proxy import server
    from memor.proxy.forward import ForwardResponse
    
    async def fake_forward(*, method, url, headers, content, stream):
        assert "api.openai.com" in url
        assert "authorization" in {k.lower() for k in headers}
        # return non-stream JSON response shape
        response_body = b'{"id":"chatcmpl-123","object":"chat.completion","choices":[{"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}'
        return ForwardResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            content=response_body
        )
    
    monkeypatch.setattr(server, "forward_request", fake_forward)
    e = FakeEmbedder(dim=16)
    app = create_proxy_app(str(tmp_path / "m.db"), embedder=e)
    c = TestClient(app)
    
    # Test with OpenAI format including trailing tool messages
    body = {
        "model": "gpt-4",
        "messages": [
            {"role": "user", "content": "What's the weather?"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call_123", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_123", "content": "Sunny, 72F"},
        ],
    }
    r = c.post("/v1/chat/completions", json=body, headers={"authorization": "Bearer test-key"})
    assert r.status_code == 200


def test_chat_completions_streaming(tmp_path, monkeypatch):
    """Test OpenAI streaming chat completions."""
    from memor.proxy import server
    from memor.proxy.forward import StreamingForwardResponse
    
    async def fake_forward(*, method, url, headers, content, stream):
        assert "api.openai.com" in url
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
                yield b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","choices":[{"delta":{"content":"hi"},"index":0}]}\n\n'
                yield b'data: [DONE]\n\n'
        
        return MockStreamingResponse()
    
    monkeypatch.setattr(server, "forward_request", fake_forward)
    e = FakeEmbedder(dim=16)
    app = create_proxy_app(str(tmp_path / "m.db"), embedder=e)
    c = TestClient(app)
    body = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    r = c.post("/v1/chat/completions", json=body, headers={"authorization": "Bearer test-key"})
    assert r.status_code == 200
    # Verify we got streaming content
    content = r.content
    assert b"chat.completion.chunk" in content
