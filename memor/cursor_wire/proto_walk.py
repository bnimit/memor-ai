"""Generic protobuf wire-format helpers (no .proto required)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class ProtoField:
    field_number: int
    wire_type: int
    offset: int
    end: int
    value: bytes | int


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, offset
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")
    raise ValueError("truncated varint")


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint must be non-negative")
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def encode_key(field_number: int, wire_type: int) -> bytes:
    return encode_varint((field_number << 3) | wire_type)


def encode_bytes_field(field_number: int, payload: bytes) -> bytes:
    return encode_key(field_number, 2) + encode_varint(len(payload)) + payload


def encode_string_field(field_number: int, text: str) -> bytes:
    return encode_bytes_field(field_number, text.encode("utf-8"))


def encode_varint_field(field_number: int, value: int) -> bytes:
    return encode_key(field_number, 0) + encode_varint(value)


def encode_fixed64_field(field_number: int, payload: bytes) -> bytes:
    if len(payload) != 8:
        raise ValueError("fixed64 requires 8 bytes")
    return encode_key(field_number, 1) + payload


def encode_fixed32_field(field_number: int, payload: bytes) -> bytes:
    if len(payload) != 4:
        raise ValueError("fixed32 requires 4 bytes")
    return encode_key(field_number, 5) + payload


def iter_fields(data: bytes):
    offset = 0
    while offset < len(data):
        start = offset
        try:
            tag, offset = read_varint(data, offset)
        except ValueError:
            break
        field_number = tag >> 3
        wire_type = tag & 0x7
        if field_number == 0:
            break
        if wire_type == 0:
            value, offset = read_varint(data, offset)
            yield ProtoField(field_number, wire_type, start, offset, value)
        elif wire_type == 1:
            if offset + 8 > len(data):
                break
            yield ProtoField(
                field_number, wire_type, start, offset + 8, data[offset : offset + 8]
            )
            offset += 8
        elif wire_type == 2:
            length, offset = read_varint(data, offset)
            if offset + length > len(data):
                break
            chunk = data[offset : offset + length]
            offset += length
            yield ProtoField(field_number, wire_type, start, offset, chunk)
        elif wire_type == 5:
            if offset + 4 > len(data):
                break
            yield ProtoField(
                field_number, wire_type, start, offset + 4, data[offset : offset + 4]
            )
            offset += 4
        else:
            break


def parse_complete_protobuf(data: bytes) -> list[ProtoField] | None:
    """Return fields if data is a complete protobuf message; else None."""
    if not data:
        return None
    fields: list[ProtoField] = []
    end = 0
    for field in iter_fields(data):
        fields.append(field)
        end = field.end
    if not fields or end != len(data):
        return None
    return fields


def walk_protobuf_strings(
    data: bytes,
    *,
    min_len: int = 40,
    _depth: int = 0,
    _max_depth: int = 32,
) -> list[str]:
    """Recursively collect UTF-8 string fields from protobuf bytes."""
    if _depth > _max_depth:
        return []
    found: list[str] = []
    for field in iter_fields(data):
        if field.wire_type != 2 or not isinstance(field.value, bytes):
            continue
        chunk = field.value
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            found.extend(
                walk_protobuf_strings(chunk, min_len=min_len, _depth=_depth + 1)
            )
            continue
        if len(text) >= min_len:
            found.append(text)
        if _looks_like_hex_agent_payload(text):
            try:
                inner = bytes.fromhex(text)
            except ValueError:
                inner = b""
            if inner:
                found.extend(
                    walk_protobuf_strings(
                        inner, min_len=min_len, _depth=_depth + 1
                    )
                )
        else:
            found.extend(
                walk_protobuf_strings(chunk, min_len=min_len, _depth=_depth + 1)
            )
    return found


_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def _looks_like_hex_agent_payload(text: str) -> bool:
    return len(text) >= 20 and len(text) % 2 == 0 and _HEX_RE.match(text) is not None


def looks_like_hex_agent_payload(text: str) -> bool:
    return _looks_like_hex_agent_payload(text)


def is_probably_utf8_string(data: bytes) -> bool:
    """True when bytes look like human text rather than nested protobuf."""
    if not data:
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if _looks_like_hex_agent_payload(text):
        return False
    printable = sum(ch.isprintable() or ch in "\n\r\t" for ch in text)
    return (printable / len(text)) >= 0.85


def extract_printable_strings(data: bytes, min_len: int = 40) -> list[str]:
    """Return unique long strings from protobuf, including JSON tool payloads."""
    raw = walk_protobuf_strings(data, min_len=min_len)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
        if item.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(item)
                out.extend(_strings_from_json(parsed, min_len=min_len, seen=seen))
            except json.JSONDecodeError:
                pass
    return out


def _strings_from_json(value, *, min_len: int, seen: set[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, str) and len(value) >= min_len and value not in seen:
        seen.add(value)
        found.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            found.extend(_strings_from_json(v, min_len=min_len, seen=seen))
    elif isinstance(value, list):
        for v in value:
            found.extend(_strings_from_json(v, min_len=min_len, seen=seen))
    return found
