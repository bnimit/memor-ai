"""HTTP request forwarding for the proxy server."""
from __future__ import annotations
from typing import AsyncIterator, Mapping
import httpx


# Connection-scoped headers that are meaningless (or harmful) to relay.
_HOP_BY_HOP = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
})

# Headers that describe the framing/encoding of a body we re-serialize.
_BODY_FRAMING = frozenset({"content-length", "content-encoding"})


def sanitize_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Drop hop-by-hop and body-framing headers before forwarding upstream.

    The proxy rewrites the request body, so the client's Content-Length and
    Content-Encoding no longer describe what we send; httpx recomputes them.
    """
    drop = _HOP_BY_HOP | _BODY_FRAMING | {"host"}
    return {k: v for k, v in headers.items() if k.lower() not in drop}


def sanitize_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Drop hop-by-hop and body-framing headers from an upstream response.

    httpx transparently decodes the upstream body, so relaying the original
    Content-Encoding/Content-Length would describe bytes the client never sees.
    """
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP | _BODY_FRAMING}


class StreamingForwardResponse:
    """Streaming response that keeps the client alive during iteration."""
    
    def __init__(self, method: str, url: str, headers: dict[str, str], content: bytes):
        self._method = method
        self._url = url
        self._headers = headers
        self._content = content
        self._client = None
        self._stream_ctx = None
        self._stream_response = None
        self.status_code = None
        self.headers = None
    
    async def __aenter__(self):
        """Enter async context - initiate the request."""
        self._client = httpx.AsyncClient(timeout=None)
        stream_ctx = self._client.stream(
            method=self._method,
            url=self._url,
            headers=self._headers,
            content=self._content,
            follow_redirects=True,
        )
        self._stream_response = await stream_ctx.__aenter__()
        self.status_code = self._stream_response.status_code
        self.headers = dict(self._stream_response.headers)
        self._stream_ctx = stream_ctx
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context - close the stream and client."""
        if self._stream_ctx:
            await self._stream_ctx.__aexit__(exc_type, exc_val, exc_tb)
        if self._client:
            await self._client.aclose()
    
    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Iterate response bytes."""
        async for chunk in self._stream_response.aiter_bytes():
            yield chunk


class ForwardResponse:
    """Non-streaming response with buffered content."""
    
    def __init__(self, status_code: int, headers: dict, content: bytes):
        self.status_code = status_code
        self.headers = headers
        self._content = content
    
    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Iterate response bytes."""
        yield self._content
    
    async def aread(self) -> bytes:
        """Read full response content."""
        return self._content


async def forward_request(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    content: bytes,
    stream: bool = False,
):
    """Forward an HTTP request to the upstream API.
    
    Args:
        method: HTTP method (e.g., "POST")
        url: Full upstream URL
        headers: Request headers dict
        content: Request body bytes
        stream: Whether to stream the response
    
    Returns:
        If stream=True: StreamingForwardResponse (use as async context manager)
        If stream=False: ForwardResponse with buffered content
    """
    if not stream:
        # Non-streaming: read full response and close client immediately
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                content=content,
                follow_redirects=True,
            )
            content_bytes = await response.aread()
            return ForwardResponse(response.status_code, dict(response.headers), content_bytes)
    else:
        # Streaming: return context manager that keeps client alive
        return StreamingForwardResponse(method, url, headers, content)
