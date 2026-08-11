"""Advertise the retrieve tool alongside the marker that promises it.

The compression path writes `[memor:ccr:<id>]` above a shortened payload, which
tells the model an original exists. Whether the model can actually fetch it
depends on a separate MCP server the user may never have installed: nothing on
the proxy path adds a tool to the request. So the marker can be a promise with
nothing behind it, and that is the reason the source guard has to stay shut --
a shortened file with no way back is exactly the failure it prevents.

This closes that gap by declaring the tool in the request itself, so the
affordance travels with the marker that needs it.

Two rules shape the implementation:

Only when a marker is actually present. A tool declaration costs tokens on
every request and changes the cached prefix, so advertising it on requests with
nothing to retrieve would be a permanent tax to serve a case that cannot arise.

Appended last, never inserted. Tool order is part of the prefix a provider
caches; putting ours in the middle would rewrite the agent's own tools and turn
cache reads into writes, which on a 99.7%-cache-read workload costs more than
compression saves.
"""
from __future__ import annotations

TOOL_NAME = "memor_retrieve"

#: Written for a reader who has just met `[memor:ccr:...]` in a tool result and
#: has no other documentation. It states the exact trigger, because a tool the
#: model never thinks to call is the same as no tool at all.
TOOL_DESCRIPTION = (
    "Retrieve the complete original text of a tool result that memor shortened. "
    "A shortened result begins with a marker like [memor:ccr:abc123]. Call this "
    "with that id whenever you need the full content — for example before "
    "editing a file whose contents were shortened, or when the omitted lines "
    "matter for your answer. Returns the original bytes exactly as the tool "
    "produced them."
)

_ANTHROPIC_TOOL = {
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "input_schema": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "The id from a [memor:ccr:<id>] marker.",
            }
        },
        "required": ["id"],
    },
}

_OPENAI_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "parameters": _ANTHROPIC_TOOL["input_schema"],
    },
}


def _already_declared(tools: list) -> bool:
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name") or (tool.get("function") or {}).get("name")
        if name == TOOL_NAME:
            return True
    return False


def inject_retrieve_tool(provider: str, body: dict, *, has_marker: bool) -> dict:
    """Declare `memor_retrieve` when the request carries a CCR marker.

    Returns the body unchanged when there is nothing to retrieve, when the tool
    is already declared, or when the request declares no tools at all — an agent
    that sends no tools is not in a position to call one, and inventing a tools
    array for it would change the request shape rather than extend it.
    """
    if not has_marker:
        return body

    tools = body.get("tools")
    if not isinstance(tools, list) or not tools:
        return body
    if _already_declared(tools):
        return body

    tool = _OPENAI_TOOL if provider == "openai" else _ANTHROPIC_TOOL
    updated = dict(body)
    updated["tools"] = [*tools, tool]
    return updated


def body_has_marker(body: dict, marker_prefix: str) -> bool:
    """True when any tool result in the request was shortened by memor.

    Scans tool results only. A marker quoted in ordinary prose is the model
    talking about compression, not a payload that needs the affordance.
    """
    for message in body.get("messages") or []:
        content = message.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                inner = block.get("content")
                if isinstance(inner, str) and marker_prefix in inner:
                    return True
                if isinstance(inner, list):
                    for part in inner:
                        if (isinstance(part, dict)
                                and marker_prefix in (part.get("text") or "")):
                            return True
            # OpenAI delivers tool output as a role=tool message with string content
            elif block.get("type") == "text" and message.get("role") == "tool":
                if marker_prefix in (block.get("text") or ""):
                    return True

    for message in body.get("messages") or []:
        if message.get("role") == "tool" and isinstance(message.get("content"), str):
            if marker_prefix in message["content"]:
                return True
    return False
