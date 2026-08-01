"""Minimal MCP server exposing memor_retrieve tool for CCR blob access."""
import json
import sys
from memor.store.sqlite_store import SqliteStore


def retrieve(blob_id: str, store: SqliteStore) -> str:
    """Retrieve CCR blob by ID or return miss message."""
    text = store.ccr_get(blob_id)
    if text is None:
        return f"memor: CCR miss for {blob_id} (expired or unknown)"
    return text


def handle_tools_list() -> dict:
    """Return list of available tools."""
    return {
        "tools": [
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
    import os
    
    # Get store path from env or default
    store_path = os.environ.get("MEMOR_DB", os.path.expanduser("~/.memor/store.db"))
    store = SqliteStore(store_path, dim=384)
    
    # Read JSON-RPC requests from stdin
    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            method = request.get("method")
            params = request.get("params", {})
            req_id = request.get("id")
            
            if method == "tools/list":
                result = handle_tools_list()
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments", {})
                result = handle_tools_call(name, arguments, store)
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
