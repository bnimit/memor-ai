from __future__ import annotations
from dataclasses import dataclass
from copy import deepcopy

@dataclass
class ToolPayload:
    """Locates a tool payload text within a request body."""
    path: list
    text: str

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
    
    last_user = messages[last_user_idx]
    content = last_user.get("content", [])
    
    # Handle string content
    if isinstance(content, str):
        return []
    
    # Extract tool_result blocks
    payloads = []
    for content_idx, block in enumerate(content):
        if isinstance(block, dict) and block.get("type") == "tool_result":
            text = block.get("content")
            
            # Handle list of content parts (join text parts)
            if isinstance(text, list):
                text_parts = []
                for part in text:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)
                text = "".join(text_parts)
            
            # Only process if we have a string
            if isinstance(text, str):
                path = ["messages", last_user_idx, "content", content_idx, "content"]
                payloads.append(ToolPayload(path=path, text=text))
    
    return payloads

def _extract_openai_latest(body: dict) -> list[ToolPayload]:
    """Extract trailing contiguous tool-role messages."""
    messages = body.get("messages", [])
    if not messages:
        return []
    
    # Find the trailing tool messages (contiguous from the end)
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
            path = ["messages", i, "content"]
            # Prepend since we're iterating backwards
            payloads.insert(0, ToolPayload(path=path, text=content))
    
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
