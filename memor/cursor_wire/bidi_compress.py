"""Compress long strings inside Cursor BidiAppend protobuf bodies."""
from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field

from memor.compress import compress_text
from memor.cursor_wire.bidi_decode import (
    COMPRESSIBLE_KINDS,
    classify_agent_message,
    maybe_gunzip,
    parse_bidi_append_request,
)
from memor.cursor_wire.proto_walk import (
    encode_bytes_field,
    encode_fixed32_field,
    encode_fixed64_field,
    encode_string_field,
    encode_varint_field,
    is_probably_utf8_string,
    looks_like_hex_agent_payload,
    parse_complete_protobuf,
)
from memor.tokencount import count_tokens

# Skip tiny blobs; heartbeats / paths stay untouched.
DEFAULT_MIN_STRING_LEN = 200
# When memor.compress is a no-op on plain text, keep head+tail for large reads.
_PLAIN_HEAD_LINES = 80
_PLAIN_TAIL_LINES = 40
_PLAIN_MIN_LINES_FOR_TRIM = 140
_PATH_RE = re.compile(r"^(?:/[^\n]{1,400}|[A-Za-z]:\\[^\n]{1,400})$")


@dataclass
class _Stats:
    tokens_before: int = 0
    tokens_after: int = 0
    content_types: dict[str, int] = field(default_factory=dict)
    strings_touched: int = 0


@dataclass
class CompressRewriteResult:
    body: bytes
    tokens_before: int
    tokens_after: int
    content_types: dict[str, int]
    message_kind: str | None
    modified: bool
    passthrough: bool
    was_gzip: bool


def rewrite_bidi_append_body(
    raw: bytes,
    *,
    min_string_len: int = DEFAULT_MIN_STRING_LEN,
) -> CompressRewriteResult:
    """Gunzip → compress agent strings → rebuild BidiAppendRequest → optional gzip."""
    body, was_gzip = maybe_gunzip(raw)
    request_id, append_seqno, agent_bytes = parse_bidi_append_request(body)
    kind = classify_agent_message(agent_bytes or b"")

    if not agent_bytes or kind not in COMPRESSIBLE_KINDS:
        return CompressRewriteResult(
            body=raw,
            tokens_before=0,
            tokens_after=0,
            content_types={},
            message_kind=kind,
            modified=False,
            passthrough=True,
            was_gzip=was_gzip,
        )

    stats = _Stats()
    new_agent = _rewrite_protobuf(agent_bytes, stats, min_string_len=min_string_len)
    modified = new_agent != agent_bytes and stats.tokens_after < stats.tokens_before

    if not modified:
        return CompressRewriteResult(
            body=raw,
            tokens_before=stats.tokens_before,
            tokens_after=stats.tokens_before,
            content_types={},
            message_kind=kind,
            modified=False,
            passthrough=True,
            was_gzip=was_gzip,
        )

    rebuilt = _encode_bidi_append_request(
        agent_bytes=new_agent,
        request_id=request_id,
        append_seqno=append_seqno,
        prefer_hex=True,
    )
    out = gzip.compress(rebuilt) if was_gzip else rebuilt
    return CompressRewriteResult(
        body=out,
        tokens_before=stats.tokens_before,
        tokens_after=stats.tokens_after,
        content_types=dict(stats.content_types),
        message_kind=kind,
        modified=True,
        passthrough=False,
        was_gzip=was_gzip,
    )


def _encode_bidi_append_request(
    *,
    agent_bytes: bytes,
    request_id: str | None,
    append_seqno: int | None,
    prefer_hex: bool = True,
) -> bytes:
    parts: list[bytes] = []
    if prefer_hex:
        parts.append(encode_string_field(1, agent_bytes.hex()))
    else:
        parts.append(encode_bytes_field(1, agent_bytes))
    if request_id is not None:
        # request_id wrapper: field 2 = message { field 1 = string }
        parts.append(encode_bytes_field(2, encode_string_field(1, request_id)))
    if append_seqno is not None:
        parts.append(encode_varint_field(3, append_seqno))
    return b"".join(parts)


def _rewrite_protobuf(data: bytes, stats: _Stats, *, min_string_len: int) -> bytes:
    fields = parse_complete_protobuf(data)
    if fields is None:
        return data
    parts: list[bytes] = []
    for fld in fields:
        if fld.wire_type == 0 and isinstance(fld.value, int):
            parts.append(encode_varint_field(fld.field_number, fld.value))
        elif fld.wire_type == 1 and isinstance(fld.value, bytes):
            parts.append(encode_fixed64_field(fld.field_number, fld.value))
        elif fld.wire_type == 5 and isinstance(fld.value, bytes):
            parts.append(encode_fixed32_field(fld.field_number, fld.value))
        elif fld.wire_type == 2 and isinstance(fld.value, bytes):
            new_val = _rewrite_length_delimited(
                fld.value, stats, min_string_len=min_string_len
            )
            parts.append(encode_bytes_field(fld.field_number, new_val))
        else:
            return data
    return b"".join(parts)


def _rewrite_length_delimited(
    value: bytes, stats: _Stats, *, min_string_len: int
) -> bytes:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        return _rewrite_protobuf(value, stats, min_string_len=min_string_len)

    if looks_like_hex_agent_payload(text):
        try:
            inner = bytes.fromhex(text)
        except ValueError:
            return value
        new_inner = _rewrite_protobuf(inner, stats, min_string_len=min_string_len)
        return new_inner.hex().encode("ascii")

    if is_probably_utf8_string(value):
        if len(text) < min_string_len or _PATH_RE.match(text.strip()):
            return value
        compressed = _compress_string(text, stats)
        return compressed.encode("utf-8")

    return _rewrite_protobuf(value, stats, min_string_len=min_string_len)


def _looks_like_source(text: str) -> bool:
    """Avoid running log crushers on source / markdown file reads."""
    markers = (
        "def ",
        "class ",
        "import ",
        "from __future__",
        "function ",
        "const ",
        "package ",
        "```",
        "#include",
    )
    hits = sum(1 for m in markers if m in text)
    return hits >= 2


def _compress_string(text: str, stats: _Stats) -> str:
    before = count_tokens(text)
    candidate = text
    content_type = "text"

    # Source/markdown reads: head/tail trim only (log crushers mangle code).
    if not _looks_like_source(text):
        result = compress_text(text)
        if (
            not result.passthrough
            and result.text != text
            and result.tokens_after < before
            and result.content_type in {"log", "json", "search"}
        ):
            candidate = result.text
            content_type = result.content_type

    if candidate == text:
        candidate = _shrink_plain_text(text)
        content_type = "text"

    after = count_tokens(candidate)
    if after >= before:
        stats.tokens_before += before
        stats.tokens_after += before
        return text

    stats.tokens_before += before
    stats.tokens_after += after
    stats.strings_touched += 1
    stats.content_types[content_type] = stats.content_types.get(content_type, 0) + 1
    return candidate


def _shrink_plain_text(text: str) -> str:
    """Light normalization + head/tail keep for large file reads."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    collapsed: list[str] = []
    blank_run = 0
    for ln in lines:
        if not ln:
            blank_run += 1
            if blank_run <= 1:
                collapsed.append(ln)
            continue
        blank_run = 0
        collapsed.append(ln)

    if len(collapsed) < _PLAIN_MIN_LINES_FOR_TRIM:
        out = "\n".join(collapsed)
        return out if out != text else text

    head = collapsed[:_PLAIN_HEAD_LINES]
    tail = collapsed[-_PLAIN_TAIL_LINES:]
    omitted = len(collapsed) - _PLAIN_HEAD_LINES - _PLAIN_TAIL_LINES
    if omitted <= 0:
        return "\n".join(collapsed)
    marker = f"\n\n[memor:truncated omitted_lines={omitted}]\n\n"
    return "\n".join(head) + marker + "\n".join(tail)
