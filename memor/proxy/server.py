"""HTTP proxy server for Anthropic API with context compression."""
from __future__ import annotations
import json
import time
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from memor.store.sqlite_store import SqliteStore, read_dim
from memor.proxy.forward import (
    forward_request,
    sanitize_request_headers,
    sanitize_response_headers,
)
from memor.proxy.shim import compressor_state, prepare_request_body
from memor.proxy.upstream import resolve_agent, resolve_upstream_url
from memor.proxy.usage import (
    UsageSniffer,
    usage_from_anthropic,
    usage_from_openai,
)
from memor.embed.local import LocalEmbedder


def _assert_localhost(host: str) -> None:
    """Verify that the bind address is localhost only."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"refusing non-localhost bind: {host}")


#: When this process loaded. Compared against the mtime of the installed
#: sources to detect a proxy still serving code that has since changed.
_STARTED_AT = time.time()


def _wants_stream(body: dict, headers) -> bool:
    """True when the client requested SSE streaming.

    Matches body.stream and Accept headers that include text/event-stream
    (e.g. ``text/event-stream, */*``), case-insensitively.
    """
    if body.get("stream"):
        return True
    accept = ""
    try:
        accept = headers.get("accept") or headers.get("Accept") or ""
    except Exception:
        accept = ""
    return "text/event-stream" in accept.lower()


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
        """Health check, including which build is actually serving.

        A proxy is a long-lived process, and an editable install means the
        files on disk can move far ahead of the code in memory. That gap is
        invisible: the version on disk says one thing, the running process
        does another, and features appear not to work for reasons no log
        explains. Reporting the loaded version and the process start time lets
        a caller tell "not implemented" from "not restarted".
        """
        from memor import __version__

        return {
            "ok": True,
            "bind": "127.0.0.1",
            "mode": compressor_state.mode,
            "compressor_ready": compressor_state.compressor_ready,
            "version": __version__,
            "started_at": _STARTED_AT,
            # Capabilities the caller can test for directly, rather than
            # inferring them from a version string.
            "captures_stream_usage": True,
        }
    
    @app.post("/v1/messages")
    @app.post("/cursor/v1/messages")
    @app.post("/cline/v1/messages")
    @app.post("/opencode/v1/messages")
    async def messages_endpoint(request: Request):
        """Proxy endpoint for Anthropic Messages API."""
        # Parse request body
        body = await request.json()

        # A proxy is handed an HTTP request and nothing else, so the project
        # has to be read out of the request. The header is honoured when a
        # client sends one; no agent does today, and asking for it was why
        # every proxied recall resolved to "unknown" and returned nothing.
        from memor.proxy.scope import resolve_request_project
        project_hint = (request.headers.get("x-memor-project") or
                       request.headers.get("x-project") or "")
        project = resolve_request_project(project_hint, body)
        # Resolved before the pipeline runs so the recall it serves is logged
        # against the right agent and session rather than after the fact.
        agent = resolve_agent(
            request.headers, path=str(request.url.path), protocol="anthropic",
        )
        session_id = (request.headers.get("x-session-id")
                      or request.headers.get("session-id") or "")
        result = prepare_request_body(
            "anthropic", body, store,
            db_path=db_path, embedder=embedder, project=project,
            agent=agent, session_id=session_id,
        )

        upstream_headers = sanitize_request_headers(request.headers)
        
        stream = _wants_stream(body, request.headers)
        
        upstream_url = resolve_upstream_url(agent, "anthropic")
        if upstream_url is None:
            return Response(
                content=json.dumps({"error": "no upstream configured for agent"}),
                status_code=502,
                media_type="application/json",
            )
        upstream_content = json.dumps(result.body).encode("utf-8")

        # Parse usage from response for non-streaming
        upstream_usage = None

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
                    upstream_usage = usage_from_anthropic(json.loads(response_content))
                except (json.JSONDecodeError, KeyError):
                    pass

            # Record savings to database
            session_id = request.headers.get("x-session-id") or request.headers.get("session-id")

            row = {
                "timestamp": time.time(),
                "agent": agent,
                "provider": "anthropic",
                "session_id": session_id,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "content_types": result.content_types,
                "passthrough": int(result.passthrough),
                "experiment_arm": result.experiment_arm,
            }
            row.update((upstream_usage or UsageSniffer("anthropic").usage).as_row())
            store.record_proxy_savings(row)
            
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
            
            # The savings row is written before the first byte is forwarded so
            # that a dropped stream still records that compression happened.
            # Usage arrives in the stream itself and completes the row at the
            # end -- see memor/proxy/usage.py.
            session_id = request.headers.get("x-session-id") or request.headers.get("session-id")

            row_id = store.record_proxy_savings({
                "timestamp": time.time(),
                "agent": agent,
                "provider": "anthropic",
                "session_id": session_id,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "content_types": result.content_types,
                "passthrough": int(result.passthrough),
                "experiment_arm": result.experiment_arm,
            })

            # Return streaming response - context will be managed by the generator
            sniffer = UsageSniffer("anthropic")

            async def stream_with_context():
                try:
                    async for chunk in resp.aiter_bytes():
                        sniffer.feed(chunk)
                        yield chunk
                finally:
                    sniffer.close()
                    store.update_proxy_usage(row_id, sniffer.usage.as_row())
                    await streaming_ctx.__aexit__(None, None, None)
            
            return StreamingResponse(
                stream_with_context(),
                status_code=resp.status_code,
                headers=sanitize_response_headers(resp.headers),
            )
    
    @app.post("/v1/chat/completions")
    @app.post("/cursor/v1/chat/completions")
    @app.post("/cline/v1/chat/completions")
    @app.post("/opencode/v1/chat/completions")
    async def chat_completions_endpoint(request: Request):
        """Proxy endpoint for OpenAI Chat Completions API."""
        # Parse request body
        body = await request.json()

        # A proxy is handed an HTTP request and nothing else, so the project
        # has to be read out of the request. The header is honoured when a
        # client sends one; no agent does today, and asking for it was why
        # every proxied recall resolved to "unknown" and returned nothing.
        from memor.proxy.scope import resolve_request_project
        project_hint = (request.headers.get("x-memor-project") or
                       request.headers.get("x-project") or "")
        project = resolve_request_project(project_hint, body)
        # Resolved before the pipeline runs so the recall it serves is logged
        # against the right agent and session rather than after the fact.
        agent = resolve_agent(
            request.headers, path=str(request.url.path), protocol="openai",
        )
        session_id = (request.headers.get("x-session-id")
                      or request.headers.get("session-id") or "")
        result = prepare_request_body(
            "openai", body, store,
            db_path=db_path, embedder=embedder, project=project,
            agent=agent, session_id=session_id,
        )

        upstream_headers = sanitize_request_headers(request.headers)
        
        stream = _wants_stream(body, request.headers)
        
        upstream_url = resolve_upstream_url(agent, "openai")
        if upstream_url is None:
            return Response(
                content=json.dumps({"error": "no upstream configured for agent"}),
                status_code=502,
                media_type="application/json",
            )
        upstream_content = json.dumps(result.body).encode("utf-8")
        
        # Parse usage from response for non-streaming
        upstream_usage = None
        
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
                    upstream_usage = usage_from_openai(json.loads(response_content))
                except (json.JSONDecodeError, KeyError):
                    pass
            
            # Record savings to database
            session_id = request.headers.get("x-session-id") or request.headers.get("session-id")
            
            row = {
                "timestamp": time.time(),
                "agent": agent,
                "provider": "openai",
                "session_id": session_id,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "content_types": result.content_types,
                "passthrough": int(result.passthrough),
                "experiment_arm": result.experiment_arm,
            }
            row.update((upstream_usage or UsageSniffer("openai").usage).as_row())
            store.record_proxy_savings(row)
            
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
            
            # Written before forwarding; completed from the stream's own usage
            # frame once it ends. OpenAI only emits that frame when the client
            # asked for it via stream_options, so it may legitimately be absent.
            session_id = request.headers.get("x-session-id") or request.headers.get("session-id")
            
            row_id = store.record_proxy_savings({
                "timestamp": time.time(),
                "agent": agent,
                "provider": "openai",
                "session_id": session_id,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "content_types": result.content_types,
                "passthrough": int(result.passthrough),
                "experiment_arm": result.experiment_arm,
            })
            
            # Return streaming response - context will be managed by the generator
            sniffer = UsageSniffer("openai")

            async def stream_with_context():
                try:
                    async for chunk in resp.aiter_bytes():
                        sniffer.feed(chunk)
                        yield chunk
                finally:
                    sniffer.close()
                    store.update_proxy_usage(row_id, sniffer.usage.as_row())
                    await streaming_ctx.__aexit__(None, None, None)
            
            return StreamingResponse(
                stream_with_context(),
                status_code=resp.status_code,
                headers=sanitize_response_headers(resp.headers),
            )
    
    return app
