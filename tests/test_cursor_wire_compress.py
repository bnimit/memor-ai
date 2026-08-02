"""Tests for Cursor BidiAppend wire decode/compress + ledger attribution."""
from __future__ import annotations

import gzip

from memor.cursor_wire.bidi_compress import rewrite_bidi_append_body
from memor.cursor_wire.bidi_decode import decode_bidi_append_body
from memor.cursor_wire.ledger import AGENT, PROVIDER, record_wire_savings
from memor.cursor_wire.proto_walk import (
    encode_bytes_field,
    encode_string_field,
    encode_varint_field,
    walk_protobuf_strings,
)
from memor.store.sqlite_store import SqliteStore


def test_walk_protobuf_strings_nested():
    inner = encode_string_field(1, "hello nested world with enough length here")
    outer = encode_bytes_field(1, inner)
    found = walk_protobuf_strings(outer, min_len=10)
    assert any("hello nested world" in s for s in found)


def _exec_client_message_with_log_body(log_text: str) -> bytes:
    """Build a minimal exec_client_message-shaped AgentClientMessage."""
    # Nested tool result: field 1 path, field 2 content (mirrors captures).
    result_msg = (
        encode_string_field(1, "/tmp/demo.log")
        + encode_string_field(2, log_text)
        + encode_varint_field(3, len(log_text))
    )
    # exec_client_message tag is 0x12 = field 2 length-delimited on AgentClientMessage
    return encode_bytes_field(2, encode_bytes_field(7, encode_bytes_field(1, result_msg)))


def _bidi_body(agent: bytes, *, gzip_body: bool = True) -> bytes:
    req = (
        encode_string_field(1, agent.hex())
        + encode_bytes_field(2, encode_string_field(1, "req-wire-1"))
        + encode_varint_field(3, 3)
    )
    return gzip.compress(req) if gzip_body else req


def test_decode_bidi_append_with_hex_agent_payload():
    agent_inner = encode_string_field(
        1,
        '{"role":"tool","content":[{"type":"tool-result","result":"'
        + ("x" * 120)
        + '"}]}',
    )
    agent = encode_bytes_field(1, agent_inner)  # run_request-ish
    raw = _bidi_body(agent)
    decoded = decode_bidi_append_body(raw)
    assert decoded.was_gzip is True
    assert decoded.request_id == "req-wire-1"
    assert decoded.append_seqno == 3
    assert decoded.agent_payload_bytes == len(agent)
    assert decoded.message_kind == "run_request"
    assert decoded.json_blobs
    assert decoded.json_blobs[0]["role"] == "tool"


def test_rewrite_compresses_log_tool_payload_and_round_trips():
    log = "\n".join(
        f"2026-08-02 14:54:01 INFO worker handled request id={i} " + ("x" * 40)
        for i in range(80)
    )
    agent = _exec_client_message_with_log_body(log)
    assert agent[0] == 0x12  # exec_client_message
    raw = _bidi_body(agent)

    result = rewrite_bidi_append_body(raw, min_string_len=100)
    assert result.modified is True
    assert result.passthrough is False
    assert result.tokens_after < result.tokens_before
    assert result.content_types

    # Re-decode rewritten body
    again = decode_bidi_append_body(result.body)
    assert again.was_gzip is True
    assert again.request_id == "req-wire-1"
    assert again.append_seqno == 3
    assert again.message_kind == "exec_client_message"
    # Compressed content should still be present as a shorter string
    long_strings = [s for s in again.strings if len(s) >= 40 and not s.startswith("/")]
    assert long_strings
    assert all(len(s) < len(log) for s in long_strings)


def test_heartbeat_passthrough_unchanged():
    # client_heartbeat first byte 0x3A
    agent = bytes([0x3A, 0x00]) + b"\x00" * 46
    raw = _bidi_body(agent[:48] if len(agent) >= 48 else agent.ljust(48, b"\x00"))
    result = rewrite_bidi_append_body(raw)
    assert result.modified is False
    assert result.passthrough is True
    assert result.body == raw


def test_plain_text_file_read_gets_head_tail_trim():
    lines = [f"line {i}: " + ("code " * 10) for i in range(200)]
    body = "\n".join(lines)
    agent = _exec_client_message_with_log_body(body)
    # Force "text" path: no log levels / timestamps
    raw = _bidi_body(agent)
    result = rewrite_bidi_append_body(raw, min_string_len=100)
    assert result.modified is True
    assert result.tokens_after < result.tokens_before
    decoded = decode_bidi_append_body(result.body)
    joined = "\n".join(decoded.strings)
    assert "memor:truncated" in joined


def test_record_wire_savings_shows_up_by_agent(tmp_path):
    db_path = str(tmp_path / "wire.db")
    store = SqliteStore(db_path, dim=16)

    log = "\n".join(
        f"2026-08-02 10:00:00 INFO boom {i} " + ("z" * 50) for i in range(80)
    )
    raw = _bidi_body(_exec_client_message_with_log_body(log))
    result = rewrite_bidi_append_body(raw, min_string_len=80)
    assert result.modified is True

    row_id = record_wire_savings(result, store=store, session_id="wire-sess")
    assert row_id is not None

    agents = store.get_proxy_savings_by_agent(days=30)
    wire = next(a for a in agents if a["agent"] == AGENT)
    assert wire["tokens_before"] == result.tokens_before
    assert wire["tokens_after"] == result.tokens_after
    assert wire["requests"] == 1
    assert wire["pct_saved"] > 0

    # Dashboard API
    from fastapi.testclient import TestClient
    from memor.dashboard.server import create_app

    client = TestClient(create_app(db_path))
    data = client.get("/api/proxy-savings-by-agent?days=30").json()
    agents = data["agents"]
    assert any(a["agent"] == AGENT for a in agents)
    assert PROVIDER  # documented constant stays stable
    html = client.get("/").text
    assert "badge-cursor-wire" in html or "Cursor Wire" in html or "proxy-savings" in html


def test_record_wire_savings_skips_passthrough(tmp_path):
    db_path = str(tmp_path / "skip.db")
    store = SqliteStore(db_path, dim=16)
    agent = bytes([0x3A]) + b"\x00" * 47
    result = rewrite_bidi_append_body(_bidi_body(agent))
    assert record_wire_savings(result, store=store) is None
    assert store.get_proxy_savings_by_agent(days=30) == []
