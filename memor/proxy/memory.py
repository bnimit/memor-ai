"""Memory injection for proxy requests."""
from __future__ import annotations
from pathlib import Path


def inject_memory(provider: str, body: dict, *, project: str, db_path: str, embedder=None) -> dict:
    """Inject recalled memories into the latest user message.
    
    Args:
        provider: API provider ("anthropic" or "openai")
        body: Request body dict with messages
        project: Project name for memory scoping
        db_path: Path to the memor database
        embedder: Optional embedder instance (auto-discovered if None)
        
    Returns:
        Modified body dict with memories appended to last user message,
        or original body if no memories found.
    """
    from memor.recall import recall
    
    # Extract messages
    messages = body.get("messages", [])
    if not messages:
        return body
    
    # Find the last user message
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break
    
    if last_user_idx is None:
        return body
    
    # Get the query text from the last user message
    user_msg = messages[last_user_idx]
    content = user_msg.get("content", "")
    if not content:
        return body
    
    # If the database doesn't exist, return unchanged
    if not Path(db_path).exists():
        return body
    
    # Get or create embedder
    if embedder is None:
        try:
            from memor.embed.local import LocalEmbedder
            embedder = LocalEmbedder()
        except Exception:
            return body
    
    # Recall memories
    try:
        result = recall(
            content, project, db_path,
            embedder=embedder,
            k=5,  # Fewer results for proxy injection
            threshold=0.0,  # Use min_similarity filter instead
            max_tokens=800  # Conservative token budget
        )
    except Exception:
        # If recall fails for any reason, return unchanged
        return body
    
    # If no hits, return unchanged
    if result.hits_count == 0:
        return body
    
    # Extract just the "## Recalled Memories" section from formatted_context
    # The formatted_context has format:
    # ## Recalled Memories (project: ...)
    # 
    # ### 1. [kind] text
    # Source: ... | score: ...
    # ...
    # ---
    # Memor: recalled N memories...
    
    # We want everything up to (but not including) the final "---" line
    lines = result.formatted_context.split("\n")
    memory_lines = []
    for line in lines:
        if line.strip() == "---":
            break
        memory_lines.append(line)
    
    memories_text = "\n".join(memory_lines).strip()
    if not memories_text:
        return body
    
    # Append memories to the user message content
    modified_content = f"{content}\n\n{memories_text}"
    
    # Create modified body with updated message
    modified_body = body.copy()
    modified_messages = messages.copy()
    modified_msg = user_msg.copy()
    modified_msg["content"] = modified_content
    modified_messages[last_user_idx] = modified_msg
    modified_body["messages"] = modified_messages
    
    return modified_body
