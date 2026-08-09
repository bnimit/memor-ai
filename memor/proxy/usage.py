"""Upstream token usage, recovered from streaming and non-streaming responses.

Every ``upstream_*`` column in the ledger was NULL, and the reason was written
into the code as a comment: usage was recorded as "not available for
streaming". Agents stream essentially all of their traffic, so the columns that
exist to answer "did compression actually save money, net of the provider's
prompt cache" were never populated on a single real request.

They are available. Both providers report usage inside the event stream; it
simply arrives after the headers, which is why a handler that writes its ledger
row before forwarding bytes can never see it. This module sniffs usage out of
SSE frames as they pass through, so the ledger row can be completed once the
stream ends.

Why cache counters matter more than the gross figure: compressing a payload
that appears in more than one request changes the prompt prefix, and the
provider must re-cache. A cache read is billed at a fraction of a fresh input
token, so shrinking a prefix that was being read from cache can cost more than
it saves. Gross savings cannot see that. ``cache_creation`` versus
``cache_read`` can.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class UpstreamUsage:
    """Token counts as the provider billed them."""

    input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    output_tokens: int | None = None

    def is_empty(self) -> bool:
        return all(
            v is None
            for v in (
                self.input_tokens,
                self.cache_read_tokens,
                self.cache_creation_tokens,
                self.output_tokens,
            )
        )

    def merge(self, other: "UpstreamUsage") -> None:
        """Take any field the other observation filled in.

        Anthropic splits usage across two frames: ``message_start`` carries the
        input and cache counters, ``message_delta`` carries the final output
        count. Neither is complete on its own.
        """
        for field in (
            "input_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "output_tokens",
        ):
            value = getattr(other, field)
            if value is not None:
                setattr(self, field, value)

    def as_row(self) -> dict:
        return {
            "upstream_input_tokens": self.input_tokens,
            "upstream_cache_read_tokens": self.cache_read_tokens,
            "upstream_cache_creation_tokens": self.cache_creation_tokens,
            "upstream_output_tokens": self.output_tokens,
        }


def _as_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def usage_from_anthropic(payload: dict) -> UpstreamUsage:
    """Usage from an Anthropic message body or a streamed frame's payload."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        message = payload.get("message")
        if isinstance(message, dict):
            usage = message.get("usage")
    if not isinstance(usage, dict):
        return UpstreamUsage()
    return UpstreamUsage(
        input_tokens=_as_int(usage.get("input_tokens")),
        cache_read_tokens=_as_int(usage.get("cache_read_input_tokens")),
        cache_creation_tokens=_as_int(usage.get("cache_creation_input_tokens")),
        output_tokens=_as_int(usage.get("output_tokens")),
    )


def usage_from_openai(payload: dict) -> UpstreamUsage:
    """Usage from an OpenAI completion body or a streamed chunk.

    OpenAI reports cached input inside ``prompt_tokens_details.cached_tokens``
    and counts those tokens in ``prompt_tokens`` as well, unlike Anthropic
    which reports cache reads separately from ``input_tokens``. The two are
    normalized here so a single net-of-cache formula can serve both.
    """
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return UpstreamUsage()
    prompt = _as_int(usage.get("prompt_tokens"))
    cached = None
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = _as_int(details.get("cached_tokens"))
    uncached = prompt
    if prompt is not None and cached is not None:
        uncached = max(0, prompt - cached)
    return UpstreamUsage(
        input_tokens=uncached,
        cache_read_tokens=cached,
        cache_creation_tokens=None,
        output_tokens=_as_int(usage.get("completion_tokens")),
    )


class UsageSniffer:
    """Extracts usage from an SSE byte stream without altering it.

    The stream is forwarded to the agent verbatim; this only reads. Frames are
    reassembled across chunk boundaries because an SSE ``data:`` line is not
    guaranteed to arrive whole, and a JSON fragment parsed on its own yields
    nothing rather than an error anyone would notice.
    """

    #: A frame larger than this is not a usage frame; refuse to buffer it.
    _MAX_BUFFER = 1 << 20

    def __init__(self, protocol: str) -> None:
        self.protocol = protocol
        self.usage = UpstreamUsage()
        self._buffer = b""

    def feed(self, chunk: bytes) -> None:
        """Observe one chunk of the upstream stream. Never raises."""
        try:
            self._feed(chunk)
        except Exception:
            # Bookkeeping must never break the stream the user is reading.
            pass

    def _feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._buffer += chunk
        # Keep only the trailing partial line; whole lines are consumed now.
        *lines, self._buffer = self._buffer.split(b"\n")
        if len(self._buffer) > self._MAX_BUFFER:
            self._buffer = b""
        for line in lines:
            self._consume_line(line)

    def _consume_line(self, line: bytes) -> None:
        stripped = line.strip()
        if not stripped.startswith(b"data:"):
            return
        data = stripped[len(b"data:"):].strip()
        if not data or data == b"[DONE]":
            return
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return
        self.observe(payload)

    def observe(self, payload: dict) -> None:
        """Merge usage from one decoded frame."""
        if self.protocol == "anthropic":
            found = usage_from_anthropic(payload)
        else:
            found = usage_from_openai(payload)
        if not found.is_empty():
            self.usage.merge(found)

    def close(self) -> None:
        """Flush any trailing frame that arrived without a newline."""
        if self._buffer:
            buffered, self._buffer = self._buffer, b""
            try:
                self._consume_line(buffered)
            except Exception:
                pass
