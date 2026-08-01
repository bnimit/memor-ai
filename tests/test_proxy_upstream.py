from __future__ import annotations

import memor.config as cfg
from memor.proxy import upstream


def _patch_config(tmp_path, monkeypatch, upstreams: dict | None = None):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
    if upstreams is not None:
        cfg.save_config({**cfg._DEFAULTS, "proxy_upstreams": upstreams})


class TestResolveAgent:
    def test_x_agent_header(self):
        assert upstream.resolve_agent({"x-agent": "goose"}) == "goose"

    def test_agent_header_fallback(self):
        assert upstream.resolve_agent({"agent": "kimi"}) == "kimi"

    def test_x_agent_takes_precedence(self):
        headers = {"x-agent": "goose", "agent": "kimi"}
        assert upstream.resolve_agent(headers) == "goose"

    def test_unknown_when_missing(self):
        assert upstream.resolve_agent({}) == "unknown"

    def test_case_insensitive(self):
        assert upstream.resolve_agent({"X-Agent": "goose"}) == "goose"
        assert upstream.resolve_agent({"Agent": "kimi"}) == "kimi"


class TestResolveUpstreamUrl:
    def test_agent_specific_upstream(self, tmp_path, monkeypatch):
        _patch_config(
            tmp_path,
            monkeypatch,
            {
                "goose": {
                    "protocol": "openai",
                    "base_url": "https://api.deepseek.com/v1/chat/completions",
                    "provider_name": "custom_deepseek",
                },
                "kimi": {
                    "protocol": "openai",
                    "base_url": "https://api.moonshot.cn/v1/chat/completions",
                    "provider_name": "kimi",
                },
            },
        )
        assert (
            upstream.resolve_upstream_url("goose", "openai")
            == "https://api.deepseek.com/v1/chat/completions"
        )
        assert (
            upstream.resolve_upstream_url("kimi", "openai")
            == "https://api.moonshot.cn/v1/chat/completions"
        )

    def test_single_upstream_fallback_for_unknown_agent(self, tmp_path, monkeypatch):
        _patch_config(
            tmp_path,
            monkeypatch,
            {
                "goose": {
                    "protocol": "openai",
                    "base_url": "https://api.deepseek.com/v1/chat/completions",
                    "provider_name": "",
                },
            },
        )
        assert (
            upstream.resolve_upstream_url("unknown", "openai")
            == "https://api.deepseek.com/v1/chat/completions"
        )

    def test_claude_anthropic_legacy_default(self, tmp_path, monkeypatch):
        _patch_config(tmp_path, monkeypatch, {})
        assert (
            upstream.resolve_upstream_url("claude", "anthropic")
            == "https://api.anthropic.com/v1/messages"
        )

    def test_codex_openai_legacy_default(self, tmp_path, monkeypatch):
        _patch_config(tmp_path, monkeypatch, {})
        assert (
            upstream.resolve_upstream_url("codex", "openai")
            == "https://api.openai.com/v1/chat/completions"
        )

    def test_no_match_returns_none(self, tmp_path, monkeypatch):
        _patch_config(tmp_path, monkeypatch, {})
        assert upstream.resolve_upstream_url("unknown", "openai") is None
        assert upstream.resolve_upstream_url("goose", "anthropic") is None

    def test_multiple_upstreams_no_fallback_for_unknown(self, tmp_path, monkeypatch):
        _patch_config(
            tmp_path,
            monkeypatch,
            {
                "goose": {
                    "protocol": "openai",
                    "base_url": "https://api.deepseek.com/v1/chat/completions",
                    "provider_name": "",
                },
                "kimi": {
                    "protocol": "openai",
                    "base_url": "https://api.moonshot.cn/v1/chat/completions",
                    "provider_name": "",
                },
            },
        )
        assert upstream.resolve_upstream_url("unknown", "openai") is None


class TestServerUpstreamRouting:
    def test_messages_routes_by_x_agent(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from memor.embed.fake import FakeEmbedder
        from memor.proxy import server
        from memor.proxy.forward import ForwardResponse

        _patch_config(
            tmp_path,
            monkeypatch,
            {
                "goose": {
                    "protocol": "anthropic",
                    "base_url": "https://custom.example.com/v1/messages",
                    "provider_name": "",
                },
            },
        )

        seen_urls: list[str] = []

        async def fake_forward(*, method, url, headers, content, stream):
            seen_urls.append(url)
            return ForwardResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                content=b'{"content":[],"usage":{"input_tokens":1,"output_tokens":1}}',
            )

        monkeypatch.setattr(server, "forward_request", fake_forward)
        app = server.create_proxy_app(str(tmp_path / "m.db"), embedder=FakeEmbedder(dim=16))
        client = TestClient(app)
        body = {
            "model": "claude-sonnet-4-0",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 16,
        }
        r = client.post(
            "/v1/messages",
            json=body,
            headers={"x-api-key": "test-key", "x-agent": "goose"},
        )
        assert r.status_code == 200
        assert seen_urls == ["https://custom.example.com/v1/messages"]

    def test_chat_completions_routes_by_x_agent(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from memor.embed.fake import FakeEmbedder
        from memor.proxy import server
        from memor.proxy.forward import ForwardResponse

        _patch_config(
            tmp_path,
            monkeypatch,
            {
                "kimi": {
                    "protocol": "openai",
                    "base_url": "https://api.moonshot.cn/v1/chat/completions",
                    "provider_name": "kimi",
                },
            },
        )

        seen_urls: list[str] = []

        async def fake_forward(*, method, url, headers, content, stream):
            seen_urls.append(url)
            return ForwardResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                content=b'{"choices":[{"message":{"content":"hi"}}],"usage":{"prompt_tokens":1,"completion_tokens":1}}',
            )

        monkeypatch.setattr(server, "forward_request", fake_forward)
        app = server.create_proxy_app(str(tmp_path / "m.db"), embedder=FakeEmbedder(dim=16))
        client = TestClient(app)
        body = {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        r = client.post(
            "/v1/chat/completions",
            json=body,
            headers={"authorization": "Bearer test-key", "x-agent": "kimi"},
        )
        assert r.status_code == 200
        assert seen_urls == ["https://api.moonshot.cn/v1/chat/completions"]
