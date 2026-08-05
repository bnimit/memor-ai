"""Memory injection for proxy requests."""
from __future__ import annotations
from pathlib import Path

from memor.types import GLOBAL_PROJECT


def _content_to_text(content) -> str:
    """Flatten a message's content into plain text for use as a recall query.

    Both providers allow a list of typed blocks; only the text ones carry a
    question worth searching on.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(p for p in parts if p)
    return ""


def _append_memories(content, memories_text: str):
    """Append memories to a message content, preserving its original shape."""
    if isinstance(content, str):
        return f"{content}\n\n{memories_text}"
    if isinstance(content, list):
        return [*content, {"type": "text", "text": memories_text}]
    return content


def _log_recall(store, *, project: str, query: str, result, agent: str,
                session_id: str, conversation_key: str = "") -> None:
    """Record a proxy-served recall, including one that found nothing.

    The hook has always logged; this path never did, and that is why recall
    could return empty on 4,056 consecutive requests without a single number
    moving anywhere. A zero-hit recall is the most informative event the
    system produces -- it is the only evidence that retrieval was asked a
    question it could not answer -- and it was the one event being discarded.

    Never raises: a ledger write must not cost a user their response.
    """
    if store is None:
        return
    try:
        store.log_recall(
            project=project, query_preview=query[:100],
            hits_count=result.hits_count, top_score=result.top_score,
            tokens_injected=result.tokens_injected,
            latency_ms=result.latency_ms, status=result.status,
            session_id=session_id or "", agent=agent,
            conversation_key=conversation_key)
        if result.hit_ids:
            store.record_recall(result.hit_ids)
    except Exception:
        pass


def inject_memory(provider: str, body: dict, *, project: str, db_path: str,
                  embedder=None, store=None, agent: str = "unknown",
                  session_id: str = "") -> dict:
    """Inject recalled memories into the latest user message.

    Args:
        provider: API provider ("anthropic" or "openai")
        body: Request body dict with messages
        project: Project name for memory scoping
        db_path: Path to the memor database
        embedder: Optional embedder instance (auto-discovered if None)
        store: Open store used to log the recall. Omit to skip logging.
        agent: Agent label for the ledger row.
        session_id: Session the request belongs to, when known.

    Returns:
        Modified body dict with memories appended to last user message,
        or original body if no memories found.
    """
    from memor.recall import recall

    messages = body.get("messages", [])
    if not messages:
        return body

    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    if last_user_idx is None:
        return body

    user_msg = messages[last_user_idx]
    content = user_msg.get("content", "")
    query = _content_to_text(content).strip()
    if not query:
        return body

    if not Path(db_path).exists():
        return body

    # The proxy sees no working directory, so an unresolved project falls back
    # to cross-project memories rather than searching a bucket that can't exist.
    if not project or project == "unknown":
        project = GLOBAL_PROJECT

    if embedder is None:
        try:
            from memor.embed.local import LocalEmbedder
            embedder = LocalEmbedder()
        except Exception:
            return body

    try:
        result = recall(
            query, project, db_path,
            embedder=embedder,
            k=5,  # Fewer results for proxy injection
            threshold=0.0,  # Use min_similarity filter instead
            max_tokens=800  # Conservative token budget
        )
    except Exception:
        # If recall fails for any reason, return unchanged
        return body

    # Claude Code sends no session header, so the episode meter has nothing to
    # join a proxy-served recall against. Both sides can derive this from the
    # conversation's opening message without any client cooperation.
    from memor.conversation import key_from_body

    _log_recall(store, project=project, query=query, result=result,
                agent=agent, session_id=session_id,
                conversation_key=key_from_body(body))

    if result.hits_count == 0:
        return body

    # formatted_context ends with a "---" separator and a status line; keep only
    # the "## Recalled Memories" section above it.
    lines = result.formatted_context.split("\n")
    memory_lines = []
    for line in lines:
        if line.strip() == "---":
            break
        memory_lines.append(line)

    memories_text = "\n".join(memory_lines).strip()
    if not memories_text:
        return body

    modified_body = body.copy()
    modified_messages = list(messages)
    modified_msg = dict(user_msg)
    modified_msg["content"] = _append_memories(content, memories_text)
    modified_messages[last_user_idx] = modified_msg
    modified_body["messages"] = modified_messages

    return modified_body
