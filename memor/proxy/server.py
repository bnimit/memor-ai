"""HTTP proxy server for Anthropic API with context compression."""
from __future__ import annotations
import json
import time
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from memor.store.sqlite_store import SqliteStore, read_dim
from memor.proxy.pipeline import run_pipeline
from memor.proxy.forward import (
    forward_request,
    sanitize_request_headers,
    sanitize_response_headers,
)
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
    
    # Store opens with the DB's recorded dim (CCR + savings don't need embeddings).
    # Fall back to the embedder dim only for a brand-new database.
    if embedder is None:
        embedder = LocalEmbedder()
    store = SqliteStore(db_path, dim=read_dim(db_path, embedder.dim))
    
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
        
        # Inject recalled memories after pipeline, before forward
        from memor.proxy.memory import inject_memory
        from memor.project import resolve_project
        project_hint = (request.headers.get("x-memor-project") or 
                       request.headers.get("x-project") or "")
        project = resolve_project(project_hint) if project_hint else "unknown"
        result.body = inject_memory(
            "anthropic", result.body, 
            project=project, db_path=db_path, embedder=embedder
        )
        
        upstream_headers = sanitize_request_headers(request.headers)
        
        # Determine if streaming is requested
        stream = body.get("stream", False) or request.headers.get("accept") == "text/event-stream"
        
        # Forward to Anthropic API
        upstream_url = "https://api.anthropic.com/v1/messages"
        upstream_content = json.dumps(result.body).encode("utf-8")
        
        # Parse usage from response for non-streaming
        upstream_input_tokens = None
        upstream_cache_read_tokens = None
        upstream_output_tokens = None
        
        if not stream:
            # Non-streaming: get response with buffered content
            upstream_response = await forward_request(
                method="POST",
                url=upstream_url,
                headers=upstream_headers,
                content=upstream_content,
                stream=False,
            )
            
            # Read response once and parse usage
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
            
            # Return non-streaming response
            return Response(
                content=response_content,
                status_code=upstream_response.status_code,
                headers=sanitize_response_headers(upstream_response.headers),
            )
        else:
            # Streaming: use context manager to keep client alive
            streaming_ctx = await forward_request(
                method="POST",
                url=upstream_url,
                headers=upstream_headers,
                content=upstream_content,
                stream=True,
            )
            
            # Enter context to get metadata
            resp = await streaming_ctx.__aenter__()
            
            # Record savings immediately (before streaming starts)
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
                "upstream_input_tokens": None,  # Not available for streaming
                "upstream_cache_read_tokens": None,
                "upstream_output_tokens": None,
            })
            
            # Return streaming response - context will be managed by the generator
            async def stream_with_context():
                try:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                finally:
                    await streaming_ctx.__aexit__(None, None, None)
            
            return StreamingResponse(
                stream_with_context(),
                status_code=resp.status_code,
                headers=sanitize_response_headers(resp.headers),
            )
    
    @app.post("/v1/chat/completions")
    async def chat_completions_endpoint(request: Request):
        """Proxy endpoint for OpenAI Chat Completions API."""
        # Parse request body
        body = await request.json()
        
        # Run compression pipeline
        result = run_pipeline("openai", body, store)
        
        # Inject recalled memories after pipeline, before forward
        from memor.proxy.memory import inject_memory
        from memor.project import resolve_project
        project_hint = (request.headers.get("x-memor-project") or 
                       request.headers.get("x-project") or "")
        project = resolve_project(project_hint) if project_hint else "unknown"
        result.body = inject_memory(
            "openai", result.body, 
            project=project, db_path=db_path, embedder=embedder
        )
        
        upstream_headers = sanitize_request_headers(request.headers)
        
        # Determine if streaming is requested
        stream = body.get("stream", False) or request.headers.get("accept") == "text/event-stream"
        
        # Forward to OpenAI API
        upstream_url = "https://api.openai.com/v1/chat/completions"
        upstream_content = json.dumps(result.body).encode("utf-8")
        
        # Parse usage from response for non-streaming
        upstream_prompt_tokens = None
        upstream_completion_tokens = None
        
        if not stream:
            # Non-streaming: get response with buffered content
            upstream_response = await forward_request(
                method="POST",
                url=upstream_url,
                headers=upstream_headers,
                content=upstream_content,
                stream=False,
            )
            
            # Read response once and parse usage
            response_content = await upstream_response.aread()
            if upstream_response.status_code == 200:
                try:
                    response_json = json.loads(response_content)
                    usage = response_json.get("usage", {})
                    upstream_prompt_tokens = usage.get("prompt_tokens")
                    upstream_completion_tokens = usage.get("completion_tokens")
                except (json.JSONDecodeError, KeyError):
                    pass
            
            # Record savings to database
            session_id = request.headers.get("x-session-id") or request.headers.get("session-id")
            agent = request.headers.get("x-agent") or request.headers.get("agent") or "unknown"
            
            store.record_proxy_savings({
                "timestamp": time.time(),
                "agent": agent,
                "provider": "openai",
                "session_id": session_id,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "content_types": result.content_types,
                "passthrough": result.passthrough,
                "upstream_input_tokens": upstream_prompt_tokens,
                "upstream_cache_read_tokens": None,
                "upstream_output_tokens": upstream_completion_tokens,
            })
            
            # Return non-streaming response
            return Response(
                content=response_content,
                status_code=upstream_response.status_code,
                headers=sanitize_response_headers(upstream_response.headers),
            )
        else:
            # Streaming: use context manager to keep client alive
            streaming_ctx = await forward_request(
                method="POST",
                url=upstream_url,
                headers=upstream_headers,
                content=upstream_content,
                stream=True,
            )
            
            # Enter context to get metadata
            resp = await streaming_ctx.__aenter__()
            
            # Record savings immediately (before streaming starts)
            session_id = request.headers.get("x-session-id") or request.headers.get("session-id")
            agent = request.headers.get("x-agent") or request.headers.get("agent") or "unknown"
            
            store.record_proxy_savings({
                "timestamp": time.time(),
                "agent": agent,
                "provider": "openai",
                "session_id": session_id,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "content_types": result.content_types,
                "passthrough": result.passthrough,
                "upstream_input_tokens": None,  # Not available for streaming
                "upstream_cache_read_tokens": None,
                "upstream_output_tokens": None,
            })
            
            # Return streaming response - context will be managed by the generator
            async def stream_with_context():
                try:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                finally:
                    await streaming_ctx.__aexit__(None, None, None)
            
            return StreamingResponse(
                stream_with_context(),
                status_code=resp.status_code,
                headers=sanitize_response_headers(resp.headers),
            )
    
    return app
