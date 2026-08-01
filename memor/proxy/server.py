"""HTTP proxy server for Anthropic API with context compression."""
from __future__ import annotations
import json
import time
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from memor.store.sqlite_store import SqliteStore
from memor.proxy.pipeline import run_pipeline
from memor.proxy.forward import forward_request
from memor.embed.local import LocalEmbedder


def _assert_localhost(host: str) -> None:
    """Verify that the bind address is localhost only."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"refusing non-localhost bind: {host}")


def create_proxy_app(db_path: str | None = None, embedder = None) -> FastAPI:
    """Create a FastAPI proxy application.
    
    Args:
        db_path: Path to the memor database. If None, uses default location.
        embedder: Optional embedder instance. If None, uses LocalEmbedder.
    
    Returns:
        Configured FastAPI application
    """
    if db_path is None:
        db_path = str(Path.home() / ".memor" / "memor.db")
    
    # Initialize store with embedding dimension
    if embedder is None:
        embedder = LocalEmbedder()
    store = SqliteStore(db_path, dim=embedder.dim)
    
    app = FastAPI()
    
    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"ok": True, "bind": "127.0.0.1"}
    
    @app.post("/v1/messages")
    async def messages_endpoint(request: Request):
        """Proxy endpoint for Anthropic Messages API."""
        # Parse request body
        body = await request.json()
        
        # Run compression pipeline
        result = run_pipeline("anthropic", body, store)
        
        # Prepare headers for upstream request
        # Copy all headers except 'host'
        upstream_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() != "host"
        }
        
        # Determine if streaming is requested
        stream = body.get("stream", False) or request.headers.get("accept") == "text/event-stream"
        
        # Forward to Anthropic API
        upstream_url = "https://api.anthropic.com/v1/messages"
        upstream_content = json.dumps(result.body).encode("utf-8")
        
        upstream_response = await forward_request(
            method="POST",
            url=upstream_url,
            headers=upstream_headers,
            content=upstream_content,
            stream=stream,
        )
        
        # Parse usage from response for non-streaming
        upstream_input_tokens = None
        upstream_cache_read_tokens = None
        upstream_output_tokens = None
        response_content = None
        
        if not stream:
            # Read response once and reuse
            response_content = await upstream_response.aread()
            if upstream_response.status_code == 200:
                try:
                    response_json = json.loads(response_content)
                    usage = response_json.get("usage", {})
                    upstream_input_tokens = usage.get("input_tokens")
                    upstream_cache_read_tokens = usage.get("cache_read_input_tokens")
                    upstream_output_tokens = usage.get("output_tokens")
                except (json.JSONDecodeError, KeyError):
                    pass
        
        # Record savings to database
        # Extract session_id from headers if available
        session_id = request.headers.get("x-session-id") or request.headers.get("session-id")
        agent = request.headers.get("x-agent") or request.headers.get("agent") or "unknown"
        
        store.record_proxy_savings({
            "timestamp": time.time(),
            "agent": agent,
            "provider": "anthropic",
            "session_id": session_id,
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
            "content_types": result.content_types,
            "passthrough": result.passthrough,
            "upstream_input_tokens": upstream_input_tokens,
            "upstream_cache_read_tokens": upstream_cache_read_tokens,
            "upstream_output_tokens": upstream_output_tokens,
        })
        
        # Return response to client
        if stream:
            async def stream_response():
                async for chunk in upstream_response.aiter_bytes():
                    yield chunk
            
            return StreamingResponse(
                stream_response(),
                status_code=upstream_response.status_code,
                headers=dict(upstream_response.headers),
            )
        else:
            # For non-streaming, return the full response
            return Response(
                content=response_content,
                status_code=upstream_response.status_code,
                headers=dict(upstream_response.headers),
            )
    
    return app
