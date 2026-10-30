"""OpenAI Responses API support: the only API current Codex speaks."""
from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

from memor.embed.fake import FakeEmbedder
from memor.proxy.adapters import (
    apply_payload_text,
    extract_all_tool_payloads,
    extract_latest_tool_payloads,
)
from memor.proxy.retrieve_tool import inject_retrieve_tool
from memor.proxy.upstream import resolve_upstream_url, responses_url
from memor.proxy.usage import UsageSniffer, usage_from_responses

_BIG_LOG = "\n".join(
    f"2026-10-30T12:00:{i % 60:02d}Z INFO request handled path=/api/items/{i} status=200"
    for i in range(400)
)


def _body(*, stream=False):
    return {
        "model": "gpt-5.5-codex",
        "instructions": "You are Codex.",
        "stream": stream,
        "input": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "why is the api slow?"}]},
            {"type": "function_call", "call_id": "call_1", "name": "read_file",
             "arguments": json.dumps({"file_path": "src/app.py"})},
            {"type": "function_call_output", "call_id": "call_1", "output": "old output"},
            {"type": "reasoning", "summary": []},
            {"type": "function_call", "call_id": "call_2", "name": "shell",
             "arguments": json.dumps({"command": ["bash", "-lc", "tail -n 400 app.log"]})},
            {"type": "function_call_output", "call_id": "call_2", "output": _BIG_LOG},
        ],
        "tools": [{"type": "function", "name": "shell", "parameters": {}}],
    }


def _isolate_config(tmp_path, monkeypatch):
    import memor.config as cfg

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)


# --- adapters -------------------------------------------------------------


def test_latest_payloads_are_the_trailing_outputs():
    payloads = extract_latest_tool_payloads("responses", _body())
    assert [p.path for p in payloads] == [["input", 5, "output"]]
    assert payloads[0].tool_name == "shell"
    assert payloads[0].file_path is None


def test_all_payloads_cover_every_string_output():
    payloads = extract_all_tool_payloads("responses", _body())
    assert [p.path for p in payloads] == [["input", 2, "output"], ["input", 5, "output"]]
    assert (payloads[0].tool_name, payloads[0].file_path) == ("read_file", "src/app.py")


def test_custom_tool_outputs_are_found_and_list_outputs_left_alone():
    body = {"input": [
        {"type": "custom_tool_call", "call_id": "c1", "name": "apply_patch", "input": "*** Begin Patch"},
        {"type": "custom_tool_call_output", "call_id": "c1", "output": "Success."},
        {"type": "function_call", "call_id": "c2", "name": "view_image", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c2",
         "output": [{"type": "input_image", "image_url": "data:..."}]},
    ]}
    payloads = extract_all_tool_payloads("responses", body)
    assert [(p.path, p.tool_name) for p in payloads] == [
        (["input", 1, "output"], "apply_patch")
    ]


def test_bare_string_input_has_no_payloads():
    assert extract_latest_tool_payloads("responses", {"input": "hello"}) == []
    assert extract_all_tool_payloads("responses", {"input": "hello"}) == []


def test_apply_payload_text_rewrites_the_output_in_place():
    body = _body()
    updated = apply_payload_text(body, ["input", 5, "output"], "short")
    assert updated["input"][5]["output"] == "short"
    assert body["input"][5]["output"] == _BIG_LOG


# --- usage ----------------------------------------------------------------


def test_usage_splits_cached_tokens_out_of_input_tokens():
    usage = usage_from_responses({"usage": {
        "input_tokens": 1000, "input_tokens_details": {"cached_tokens": 800},
        "output_tokens": 50,
    }})
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (200, 800, 50)


def test_stream_usage_comes_from_response_completed():
    sniffer = UsageSniffer("responses")
    sniffer.feed(b'event: response.created\ndata: {"type":"response.created","response":{"usage":null}}\n\n')
    sniffer.feed(b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"hi"}\n\n')
    sniffer.feed(
        b'event: response.completed\ndata: {"type":"response.completed","response":'
        b'{"usage":{"input_tokens":300,"input_tokens_details":{"cached_tokens":100},'
        b'"output_tokens":7}}}\n\n'
    )
    assert sniffer.usage.input_tokens == 200
    assert sniffer.usage.cache_read_tokens == 100
    assert sniffer.usage.output_tokens == 7


# --- upstream and tools ---------------------------------------------------


def test_responses_url_is_derived_from_the_chat_completions_upstream():
    assert responses_url("https://api.openai.com/v1/chat/completions") == "https://api.openai.com/v1/responses"
    assert responses_url("https://gw.example/v1") == "https://gw.example/v1/responses"
    assert responses_url("https://gw.example/v1/responses") == "https://gw.example/v1/responses"


def test_codex_default_upstream_resolves_for_responses(tmp_path, monkeypatch):
    _isolate_config(tmp_path, monkeypatch)
    assert resolve_upstream_url("codex", "responses") == "https://api.openai.com/v1/responses"


def test_retrieve_tool_is_not_declared_on_responses_requests():
    body = _body()
    assert inject_retrieve_tool("responses", body, has_marker=True) is body


# --- end to end -----------------------------------------------------------


def _ledger(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM proxy_savings")]
    finally:
        con.close()


def test_responses_endpoint_compresses_forwards_and_records(tmp_path, monkeypatch):
    _isolate_config(tmp_path, monkeypatch)
    from memor.proxy import server
    from memor.proxy.forward import ForwardResponse

    seen = {}

    async def fake_forward(*, method, url, headers, content, stream):
        seen["url"] = url
        seen["body"] = json.loads(content)
        return ForwardResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            content=json.dumps({"id": "resp_1", "output": [], "usage": {
                "input_tokens": 900, "input_tokens_details": {"cached_tokens": 600},
                "output_tokens": 12,
            }}).encode(),
        )

    monkeypatch.setattr(server, "forward_request", fake_forward)
    db = str(tmp_path / "m.db")
    client = TestClient(server.create_proxy_app(db, embedder=FakeEmbedder(dim=16)))
    r = client.post("/v1/responses", json=_body(),
                    headers={"authorization": "Bearer k", "x-agent": "codex"})

    assert r.status_code == 200
    assert seen["url"] == "https://api.openai.com/v1/responses"
    forwarded = seen["body"]["input"]
    assert forwarded[5]["output"].startswith("[memor:ccr:")
    assert len(forwarded[5]["output"]) < len(_BIG_LOG)
    # Older output is outside the latest turn and left byte-exact.
    assert forwarded[2]["output"] == "old output"

    (row,) = _ledger(db)
    assert row["provider"] == "responses"
    assert row["agent"] == "codex"
    assert row["model"] == "gpt-5.5-codex"
    assert row["tokens_after"] < row["tokens_before"]
    assert (row["upstream_input_tokens"], row["upstream_cache_read_tokens"],
            row["upstream_output_tokens"]) == (300, 600, 12)


def test_responses_endpoint_streams_and_completes_usage(tmp_path, monkeypatch):
    _isolate_config(tmp_path, monkeypatch)
    from memor.proxy import server

    async def fake_forward(*, method, url, headers, content, stream):
        assert stream is True
        assert url.endswith("/v1/responses")

        class Streaming:
            async def __aenter__(self):
                self.status_code = 200
                self.headers = {"content-type": "text/event-stream"}
                return self

            async def __aexit__(self, *exc):
                pass

            async def aiter_bytes(self):
                yield b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"ok"}\n\n'
                yield (b'event: response.completed\ndata: {"type":"response.completed","response":'
                       b'{"usage":{"input_tokens":50,"output_tokens":3}}}\n\n')

        return Streaming()

    monkeypatch.setattr(server, "forward_request", fake_forward)
    db = str(tmp_path / "m.db")
    client = TestClient(server.create_proxy_app(db, embedder=FakeEmbedder(dim=16)))
    r = client.post("/v1/responses", json=_body(stream=True),
                    headers={"authorization": "Bearer k", "x-agent": "codex"})

    assert r.status_code == 200
    assert b"response.completed" in r.content
    (row,) = _ledger(db)
    assert row["provider"] == "responses"
    assert (row["upstream_input_tokens"], row["upstream_output_tokens"]) == (50, 3)


def test_chat_completions_rows_record_the_model_too(tmp_path, monkeypatch):
    _isolate_config(tmp_path, monkeypatch)
    from memor.proxy import server
    from memor.proxy.forward import ForwardResponse

    async def fake_forward(*, method, url, headers, content, stream):
        return ForwardResponse(status_code=200, headers={}, content=b'{"usage":{}}')

    monkeypatch.setattr(server, "forward_request", fake_forward)
    db = str(tmp_path / "m.db")
    client = TestClient(server.create_proxy_app(db, embedder=FakeEmbedder(dim=16)))
    client.post("/v1/messages", json={"model": "claude-sonnet-5-5", "messages": [
        {"role": "user", "content": "hi"}]}, headers={"x-agent": "claude"})
    (row,) = _ledger(db)
    assert row["model"] == "claude-sonnet-5-5"


# --- recall ---------------------------------------------------------------


def _memory_db(tmp_path):
    from memor.store.sqlite_store import SqliteStore
    from memor.types import Artifact

    db_path = str(tmp_path / "mem.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="mem1", kind="memory", project="testproj", source="distill",
        text="we use FastAPI for all REST APIs", token_count=12, created_at=100.0,
        meta={"mem_type": "decision", "session_id": "old-session"},
    )
    s.add_artifacts([art], e.embed([art.text]))
    return db_path, e


def test_recall_extends_a_trailing_user_message(tmp_path):
    from memor.proxy.memory import inject_memory

    db_path, e = _memory_db(tmp_path)
    body = {"model": "m", "input": [
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "which framework should I use for APIs?"}]},
    ]}
    result = inject_memory("responses", body, project="testproj", db_path=db_path, embedder=e)
    content = result["input"][-1]["content"]
    assert content[0]["text"] == "which framework should I use for APIs?"
    assert content[-1]["type"] == "input_text"
    assert "## Recalled Memories" in content[-1]["text"]
    assert body["input"][-1]["content"] is not content


def test_recall_extends_a_bare_string_input(tmp_path):
    from memor.proxy.memory import inject_memory

    db_path, e = _memory_db(tmp_path)
    body = {"model": "m", "input": "which framework should I use for APIs?"}
    result = inject_memory("responses", body, project="testproj", db_path=db_path, embedder=e)
    assert result["input"].startswith("which framework should I use for APIs?")
    assert "## Recalled Memories" in result["input"]


def test_recall_leaves_a_mid_loop_request_alone(tmp_path):
    """The newest user message sits behind tool steps: extending it would
    rewrite a cached prefix, so nothing is injected."""
    from memor.proxy.memory import inject_memory

    db_path, e = _memory_db(tmp_path)
    body = _body()
    assert inject_memory("responses", body, project="testproj",
                         db_path=db_path, embedder=e) is body
