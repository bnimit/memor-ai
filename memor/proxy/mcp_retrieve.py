"""MCP server exposing memor's memory to agents that cannot use a recall hook.

Two tools:

* ``memor_retrieve`` -- fetch a CCR blob by id, for the compression path.
* ``memor_recall`` -- search memory. This is the only way into agents whose
  hooks cannot inject context. jcode's hooks, for instance, are observers:
  detached, fire-and-forget, stdout discarded, so they can drive ingest but can
  never hand memories back to the prompt. A tool the model calls itself is the
  channel that remains.
"""
import json
import os
import sys
from pathlib import Path
from memor.store.sqlite_store import SqliteStore, read_dim


def default_db_path() -> str:
    """The database the rest of memor writes to."""
    return os.environ.get("MEMOR_DB") or str(Path.home() / ".memor" / "memor.db")


def open_store(db_path: str | None = None) -> SqliteStore:
    """Attach to the memor database using the dimension it was built with."""
    resolved = str(Path(db_path or default_db_path()).expanduser())
    return SqliteStore(resolved, dim=read_dim(resolved, 384))


def retrieve(blob_id: str, store: SqliteStore) -> str:
    """Retrieve CCR blob by ID or return miss message."""
    text = store.ccr_get(blob_id)
    if text is None:
        return f"memor: CCR miss for {blob_id} (expired or unknown)"
    return text


def recall_memories(query: str, *, project: str = "", k: int = 5,
                    db_path: str | None = None) -> str:
    """Search memory and render the hits as text for the model.

    Failures are returned as prose rather than raised: a broken memory lookup
    should read as "no memories" to the agent, never break its tool call.
    """
    try:
        from memor.cli import _embedder
        from memor.recall import recall

        resolved = str(Path(db_path or default_db_path()).expanduser())
        if not Path(resolved).exists():
            return "memor: no memory store yet."

        if not project:
            from memor.project import resolve_project
            project = resolve_project(os.getcwd())

        result = recall(query, project, resolved, embedder=_embedder(False), k=k)
        if not result.formatted_context:
            return f'memor: no relevant memories for "{query}" in {project}.'
        return result.formatted_context
    except Exception as exc:  # never fail the agent's tool call
        return f"memor: recall unavailable ({type(exc).__name__})."


def handle_initialize() -> dict:
    """MCP handshake. Without this a client refuses to list tools at all."""
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "memor", "version": "1"},
    }


def handle_tools_list() -> dict:
    """Return list of available tools."""
    return {
        "tools": [
            {
                "name": "memor_recall",
                "description": (
                    "Search your own past sessions for relevant memories: prior "
                    "decisions, lessons, bug fixes and conventions from this and "
                    "other projects. Use it before solving a problem that may have "
                    "been solved before, or when the user refers to earlier work."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to remember about, in natural language",
                        },
                        "project": {
                            "type": "string",
                            "description": "Project to search; defaults to the working directory",
                        },
                        "k": {
                            "type": "integer",
                            "description": "How many memories to return (default 5)",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "memor_retrieve",
                "description": "Retrieve the full original content for a CCR blob by ID",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "The CCR blob ID to retrieve"
                        }
                    },
                    "required": ["id"]
                }
            }
        ]
    }


def handle_tools_call(name: str, arguments: dict, store: SqliteStore) -> dict:
    """Execute tool call."""
    if name == "memor_recall":
        query = (arguments or {}).get("query") or ""
        if not query.strip():
            return {
                "content": [{"type": "text", "text": "Missing required parameter: query"}],
                "isError": True,
            }
        text = recall_memories(
            query,
            project=(arguments.get("project") or ""),
            k=int(arguments.get("k") or 5),
        )
        return {"content": [{"type": "text", "text": text}]}

    if name != "memor_retrieve":
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Unknown tool: {name}"
                }
            ],
            "isError": True
        }
    
    blob_id = arguments.get("id")
    if not blob_id:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Missing required parameter: id"
                }
            ],
            "isError": True
        }
    
    result = retrieve(blob_id, store)
    return {
        "content": [
            {
                "type": "text",
                "text": result
            }
        ]
    }


def main():
    """MCP server main loop using JSON-RPC over stdio."""
    store = open_store()
    
    # Read JSON-RPC requests from stdin
    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            method = request.get("method")
            params = request.get("params", {})
            req_id = request.get("id")
            
            if method == "initialize":
                result = handle_initialize()
            elif method == "tools/list":
                result = handle_tools_list()
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments", {})
                result = handle_tools_call(name, arguments, store)
            elif method in ("notifications/initialized", "initialized"):
                # A notification carries no id and expects no reply.
                continue
            else:
                result = {"error": f"Unknown method: {method}"}
            
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": result
            }
            print(json.dumps(response), flush=True)
            
        except Exception as e:
            error_response = {
                "jsonrpc": "2.0",
                "id": request.get("id") if "request" in locals() else None,
                "error": {
                    "code": -32603,
                    "message": str(e)
                }
            }
            print(json.dumps(error_response), flush=True)


if __name__ == "__main__":
    main()
