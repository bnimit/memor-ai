"""HTTP request forwarding for the proxy server."""
from __future__ import annotations
import httpx


async def forward_request(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    content: bytes,
    stream: bool = False,
) -> httpx.Response:
    """Forward an HTTP request to the upstream API.
    
    Args:
        method: HTTP method (e.g., "POST")
        url: Full upstream URL
        headers: Request headers dict
        content: Request body bytes
        stream: Whether to stream the response
    
    Returns:
        httpx.Response with aiter_bytes() and aread() methods
    """
    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            content=content,
            follow_redirects=True,
        )
        
        if stream:
            # For streaming, we need to keep the response body available
            # Return the response object directly; caller will iterate aiter_bytes()
            return response
        else:
            # For non-streaming, read the full response
            # This ensures aread() and aiter_bytes() will work
            await response.aread()
            return response
