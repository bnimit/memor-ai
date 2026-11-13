"""Memory injection for proxy requests."""
from __future__ import annotations
from pathlib import Path

from memor.recall_experiment import ARM_CONTROL, ARM_TREATMENT
from memor.types import GLOBAL_PROJECT


_TEXT_BLOCKS = ("text", "input_text")


def _content_to_text(content) -> str:
    """Flatten a message's content into plain text for use as a recall query.

    Every provider allows a list of typed blocks; only the text ones carry a
    question worth searching on. The Responses API calls them ``input_text``.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in _TEXT_BLOCKS:
                parts.append(block.get("text", ""))
        return "\n".join(p for p in parts if p)
    return ""


def _append_memories(content, memories_text: str):
    """Append memories to a message content, preserving its original shape."""
    if isinstance(content, str):
        return f"{content}\n\n{memories_text}"
    if isinstance(content, list):
        block_type = "text"
        if any(isinstance(b, dict) and b.get("type") == "input_text" for b in content):
            block_type = "input_text"
        return [*content, {"type": block_type, "text": memories_text}]
    return content


def _responses_user_turn(body: dict):
    """The Responses ``input`` user turn recall may extend, or None.

    Returns ``(index, content)``; index is None when ``input`` is a bare
    string. Only a user message that is the *last* input item qualifies. In an
    agent loop the newest user message sits before the tool calls and outputs
    of later steps, and extending it there would rewrite a prefix the
    provider has already cached. Chat-shaped bodies get the same rule for free,
    because there the newest user message is the last message.
    """
    items = body.get("input")
    if isinstance(items, str):
        return None, items
    if not isinstance(items, list) or not items:
        return None
    last = items[-1]
    if (isinstance(last, dict) and last.get("role") == "user"
            and last.get("type", "message") == "message"):
        return len(items) - 1, last.get("content", "")
    return None


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
        recall_id = store.log_recall(
            project=project, query_preview=query[:100],
            hits_count=result.hits_count, top_score=result.top_score,
            tokens_injected=result.tokens_injected,
            latency_ms=result.latency_ms, status=result.status,
            session_id=session_id or "", agent=agent,
            conversation_key=conversation_key)
        if result.hit_ids:
            store.record_recall(result.hit_ids)
            # One pending row per memory served, so the verdict later hangs off
            # the recall that produced it rather than a time window.
            store.record_recall_candidates(recall_id, result.hit_ids)
    except Exception:
        pass


def inject_memory(provider: str, body: dict, *, project: str, db_path: str,
                  embedder=None, store=None, agent: str = "unknown",
                  session_id: str = "") -> dict:
    """Inject recalled memories into the latest user message.

    Args:
        provider: API provider ("anthropic", "openai" or "responses")
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

    if provider == "responses":
        turn = _responses_user_turn(body)
        if turn is None:
            return body
        last_user_idx, content = turn
        messages = body["input"]
        user_msg = messages[last_user_idx] if last_user_idx is not None else None
    else:
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

    convo_key = key_from_body(body)

    # Recall holdout: among recalls that found something, withhold a random
    # share so recall's effect on the rest of the episode can be measured. A
    # withheld recall is recorded only in the experiment table (see
    # memor.recall_experiment), and only if that write succeeds; one nobody
    # wrote down would cost the user their memory and teach nothing.
    arm = None
    if result.hits_count and store is not None:
        from memor import recall_experiment

        if recall_experiment.is_enabled():
            arm = recall_experiment.assign_arm(
                recall_experiment.experiment_key(session_id, convo_key, query))
    if arm == ARM_CONTROL:
        if store.log_recall_experiment(
                arm=arm, project=project, query_preview=query[:100],
                hits_count=result.hits_count, tokens_offered=result.tokens_injected,
                session_id=session_id, agent=agent, conversation_key=convo_key):
            return body
        arm = None

    _log_recall(store, project=project, query=query, result=result,
                agent=agent, session_id=session_id,
                conversation_key=convo_key)
    if arm == ARM_TREATMENT:
        store.log_recall_experiment(
            arm=arm, project=project, query_preview=query[:100],
            hits_count=result.hits_count, tokens_offered=result.tokens_injected,
            session_id=session_id, agent=agent, conversation_key=convo_key)

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
    if provider == "responses":
        if last_user_idx is None:
            modified_body["input"] = _append_memories(content, memories_text)
            return modified_body
        items = list(messages)
        modified_item = dict(user_msg)
        modified_item["content"] = _append_memories(content, memories_text)
        items[last_user_idx] = modified_item
        modified_body["input"] = items
        return modified_body

    modified_messages = list(messages)
    modified_msg = dict(user_msg)
    modified_msg["content"] = _append_memories(content, memories_text)
    modified_messages[last_user_idx] = modified_msg
    modified_body["messages"] = modified_messages

    return modified_body
