from __future__ import annotations
import json
from dataclasses import dataclass, field
from copy import deepcopy

#: Tool-call argument keys that name a file, in priority order. Different tools
#: spell it differently (Read/Edit use file_path, notebooks use notebook_path).
_FILE_KEYS = ("file_path", "notebook_path", "path", "filePath", "filename", "file")


@dataclass
class ToolPayload:
    """Locates a tool payload text within a request body.

    ``tool_name``/``file_path`` come from correlating a ``tool_result`` back to
    the ``tool_use`` that produced it. They are best-effort: a payload with no
    resolvable origin still compresses, it just cannot participate in
    per-file recency decisions.
    """
    path: list
    text: str
    tool_name: str | None = None
    file_path: str | None = None
    message_index: int = -1
    #: True when no later message re-reads the same file. The newest read is the
    #: one an agent is most likely about to act on, so it is left byte-exact.
    is_latest_for_file: bool = True


def extract_latest_tool_payloads(provider: str, body: dict) -> list[ToolPayload]:
    """Extract latest-turn tool payloads from provider-specific request body.

    Anthropic: Last user message's tool_result content blocks
    OpenAI: Trailing contiguous tool-role messages
    """
    if provider == "anthropic":
        return _extract_anthropic_latest(body)
    elif provider == "openai":
        return _extract_openai_latest(body)
    else:
        return []


def extract_all_tool_payloads(provider: str, body: dict) -> list[ToolPayload]:
    """Every tool payload in the request, with provenance and recency.

    The cost of an agent loop is dominated by *old* tool results: the whole
    trajectory is resent on each step, so a file dumped at step 1 is re-read on
    every step after it. Compressing only the newest payload — what
    ``extract_latest_tool_payloads`` supports — targets the smallest share of
    the context and the one the agent is most likely about to act on.

    Returns payloads in message order. Does not modify ``body``.
    """
    if provider == "anthropic":
        payloads = _extract_anthropic_all(body)
    elif provider == "openai":
        payloads = _extract_openai_all(body)
    else:
        return []
    return _mark_latest_per_file(payloads)


def _mark_latest_per_file(payloads: list[ToolPayload]) -> list[ToolPayload]:
    """Flag only the last payload for each file as latest."""
    last_index: dict[str, int] = {}
    for i, p in enumerate(payloads):
        if p.file_path:
            last_index[p.file_path] = i
    for i, p in enumerate(payloads):
        if p.file_path:
            p.is_latest_for_file = last_index[p.file_path] == i
        else:
            # Unknown origin: treat as latest so it is never compressed on a
            # recency rule it cannot participate in.
            p.is_latest_for_file = True
    return payloads


def _file_from_args(args) -> str | None:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(args, dict):
        return None
    for key in _FILE_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _anthropic_tool_use_index(messages: list) -> dict[str, tuple[str, str | None]]:
    """Map tool_use id -> (tool name, file path) across all assistant messages."""
    index: dict[str, tuple[str, str | None]] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            use_id = block.get("id")
            if not isinstance(use_id, str):
                continue
            index[use_id] = (
                block.get("name") or "unknown",
                _file_from_args(block.get("input")),
            )
    return index


def _openai_tool_call_index(messages: list) -> dict[str, tuple[str, str | None]]:
    """Map tool_call id -> (function name, file path) across assistant messages."""
    index: dict[str, tuple[str, str | None]] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            call_id = call.get("id")
            if not isinstance(call_id, str):
                continue
            fn = call.get("function") or {}
            index[call_id] = (
                fn.get("name") or "unknown",
                _file_from_args(fn.get("arguments")),
            )
    return index


def _tool_result_text(block: dict) -> str | None:
    """Flatten a tool_result's content into text, or None if not textual."""
    text = block.get("content")
    if isinstance(text, list):
        parts = []
        for part in text:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif isinstance(part, str):
                parts.append(part)
        text = "".join(parts)
    return text if isinstance(text, str) else None


def _extract_anthropic_latest(body: dict) -> list[ToolPayload]:
    """Extract tool_result blocks from the last user message."""
    messages = body.get("messages", [])
    if not messages:
        return []

    # Find the last user message
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    if last_user_idx is None:
        return []

    return _anthropic_payloads_for_message(
        messages[last_user_idx], last_user_idx, _anthropic_tool_use_index(messages)
    )


def _anthropic_payloads_for_message(
    message: dict, msg_idx: int, use_index: dict[str, tuple[str, str | None]]
) -> list[ToolPayload]:
    content = message.get("content", [])
    if not isinstance(content, list):
        return []

    payloads = []
    for content_idx, block in enumerate(content):
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        text = _tool_result_text(block)
        if text is None:
            continue
        name, file_path = use_index.get(block.get("tool_use_id"), (None, None))
        payloads.append(
            ToolPayload(
                path=["messages", msg_idx, "content", content_idx, "content"],
                text=text,
                tool_name=name,
                file_path=file_path,
                message_index=msg_idx,
            )
        )
    return payloads


def _extract_anthropic_all(body: dict) -> list[ToolPayload]:
    messages = body.get("messages", [])
    if not messages:
        return []
    use_index = _anthropic_tool_use_index(messages)
    payloads: list[ToolPayload] = []
    for idx, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        payloads.extend(_anthropic_payloads_for_message(msg, idx, use_index))
    return payloads


def _extract_openai_latest(body: dict) -> list[ToolPayload]:
    """Extract trailing contiguous tool-role messages."""
    messages = body.get("messages", [])
    if not messages:
        return []

    call_index = _openai_tool_call_index(messages)
    payloads = []
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        role = msg.get("role")

        # Stop if we hit a non-tool message
        if role != "tool":
            break

        # Extract the content
        content = msg.get("content")
        if isinstance(content, str):
            name, file_path = call_index.get(msg.get("tool_call_id"), (None, None))
            # Prepend since we're iterating backwards
            payloads.insert(0, ToolPayload(
                path=["messages", i, "content"],
                text=content,
                tool_name=name,
                file_path=file_path,
                message_index=i,
            ))

    return payloads


def _extract_openai_all(body: dict) -> list[ToolPayload]:
    messages = body.get("messages", [])
    if not messages:
        return []
    call_index = _openai_tool_call_index(messages)
    payloads: list[ToolPayload] = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        name, file_path = call_index.get(msg.get("tool_call_id"), (None, None))
        payloads.append(ToolPayload(
            path=["messages", i, "content"],
            text=content,
            tool_name=name,
            file_path=file_path,
            message_index=i,
        ))
    return payloads


def apply_payload_text(body: dict, path: list, new_text: str) -> dict:
    """Apply new text to a payload location in the body. Returns a deep copy."""
    result = deepcopy(body)

    # Navigate to the parent of the target
    current = result
    for key in path[:-1]:
        current = current[key]

    # Set the final value
    current[path[-1]] = new_text

    return result
