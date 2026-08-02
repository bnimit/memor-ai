"""Decode BidiAppend HTTP bodies captured from api2.cursor.sh."""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path

from memor.cursor_wire.proto_walk import extract_printable_strings, iter_fields


@dataclass
class BidiAppendDecoded:
    raw_bytes: int
    was_gzip: bool
    append_seqno: int | None
    request_id: str | None
    agent_payload_bytes: int
    message_kind: str | None
    strings: list[str] = field(default_factory=list)
    json_blobs: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"bytes={self.raw_bytes} gzip={self.was_gzip} "
            f"agent_payload={self.agent_payload_bytes} kind={self.message_kind}",
            f"request_id={self.request_id} seqno={self.append_seqno}",
            f"strings={len(self.strings)} json_blobs={len(self.json_blobs)}",
        ]
        for blob in self.json_blobs[:3]:
            role = blob.get("role")
            content = blob.get("content")
            preview = ""
            if isinstance(content, str):
                preview = content[:120].replace("\n", "\\n")
            elif isinstance(content, list) and content:
                preview = json.dumps(content[0])[:120]
            lines.append(f"  json role={role} preview={preview!r}")
        return "\n".join(lines)


_KIND_BY_FIRST_BYTE = {
    0x0A: "run_request",
    0x12: "exec_client_message",
    0x1A: "kv_client_message",
    0x22: "conversation_action",
    0x2A: "exec_client_control_message",
    0x32: "interaction_response",
    0x3A: "client_heartbeat",
    0x42: "prewarm_request",
}

COMPRESSIBLE_KINDS = frozenset({"exec_client_message", "run_request", "conversation_action"})


def maybe_gunzip(data: bytes) -> tuple[bytes, bool]:
    if len(data) >= 3 and data[:3] == b"\x1f\x8b\x08":
        return gzip.decompress(data), True
    return data, False


def parse_bidi_append_request(data: bytes) -> tuple[str | None, int | None, bytes | None]:
    """Parse aiserver.v1.BidiAppendRequest without generated stubs."""
    hex_data: str | None = None
    request_id: str | None = None
    append_seqno: int | None = None
    data_binary: bytes | None = None

    for fld in iter_fields(data):
        if fld.field_number == 1 and fld.wire_type == 2 and isinstance(fld.value, bytes):
            try:
                hex_data = fld.value.decode("ascii")
            except UnicodeDecodeError:
                data_binary = fld.value
        elif fld.field_number == 2 and fld.wire_type == 2 and isinstance(fld.value, bytes):
            request_id = _first_string_field(fld.value)
        elif fld.field_number == 3 and fld.wire_type == 0 and isinstance(fld.value, int):
            append_seqno = fld.value
        elif fld.field_number == 4 and fld.wire_type == 2 and isinstance(fld.value, bytes):
            data_binary = fld.value

    agent_bytes: bytes | None = None
    if hex_data:
        try:
            agent_bytes = bytes.fromhex(hex_data)
        except ValueError:
            agent_bytes = None
    if agent_bytes is None and data_binary:
        agent_bytes = data_binary
    return request_id, append_seqno, agent_bytes


def _first_string_field(data: bytes) -> str | None:
    for fld in iter_fields(data):
        if fld.wire_type == 2 and isinstance(fld.value, bytes):
            try:
                return fld.value.decode("utf-8")
            except UnicodeDecodeError:
                continue
    return None


def classify_agent_message(agent_bytes: bytes) -> str | None:
    if not agent_bytes:
        return None
    return _KIND_BY_FIRST_BYTE.get(agent_bytes[0])


def _json_blobs_from_strings(strings: list[str]) -> list[dict]:
    blobs: list[dict] = []
    seen: set[str] = set()
    for text in strings:
        if text in seen:
            continue
        if not text.lstrip().startswith("{"):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("role") in {
            "user",
            "assistant",
            "tool",
            "system",
        }:
            seen.add(text)
            blobs.append(parsed)
    return blobs


def decode_bidi_append_body(raw: bytes, *, min_string_len: int = 40) -> BidiAppendDecoded:
    body, was_gzip = maybe_gunzip(raw)
    request_id, append_seqno, agent_bytes = parse_bidi_append_request(body)
    kind = classify_agent_message(agent_bytes or b"")
    strings: list[str] = []
    if agent_bytes:
        strings = extract_printable_strings(agent_bytes, min_len=min_string_len)
    strings.extend(extract_printable_strings(body, min_len=min_string_len))
    deduped: list[str] = []
    seen: set[str] = set()
    for s in strings:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return BidiAppendDecoded(
        raw_bytes=len(raw),
        was_gzip=was_gzip,
        append_seqno=append_seqno,
        request_id=request_id,
        agent_payload_bytes=len(agent_bytes or b""),
        message_kind=kind,
        strings=deduped,
        json_blobs=_json_blobs_from_strings(deduped),
    )


def decode_bidi_payload_file(path: Path, *, min_string_len: int = 40) -> BidiAppendDecoded:
    return decode_bidi_append_body(path.read_bytes(), min_string_len=min_string_len)
