"""A join key two components can compute without talking to each other.

The proxy and the episode meter observe the same conversation from opposite
ends and share no identifier. Claude Code sends no session header, so a
proxy-logged recall carries an empty session_id; the episode meter, reading
transcripts, knows a session only as a filename. Nothing links them.

They do both see the same first user message. Hashing it gives a stable key
each side can derive alone: the proxy from the request body, the meter from the
transcript. No client cooperation, no new header, no coordination.

This is not a security boundary and the hash is not a secret -- it is a join
key, chosen short and cheap because it is computed on the request path.
"""
from __future__ import annotations

import hashlib
import re

#: Enough to make an accidental collision between two openings implausible,
#: short enough to sit in a column and an index without cost.
_KEY_CHARS = 16

_WHITESPACE = re.compile(r"\s+")

#: How much of the opening message to hash. A first message can be enormous
#: (a pasted file, a resumed summary); the head of it is already unique, and
#: bounding the input keeps the cost flat.
_HASH_CHARS = 4_000


def conversation_key(first_user_text: str) -> str:
    """Stable key for a conversation, derived from how it opened.

    Whitespace is normalized before hashing: the same message reaches the proxy
    as JSON and the meter as a transcript record, and the two must not disagree
    over a trailing newline.
    """
    if not first_user_text or not first_user_text.strip():
        return ""
    normalized = _WHITESPACE.sub(" ", first_user_text.strip())[:_HASH_CHARS]
    return hashlib.blake2b(normalized.encode("utf-8", "replace"),
                           digest_size=_KEY_CHARS // 2).hexdigest()


def _text_of(content) -> str:
    """Flatten message content to text, whatever shape it arrived in."""
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


def key_from_body(body: dict) -> str:
    """Conversation key as seen by the proxy, from a request body."""
    if not isinstance(body, dict):
        return ""
    for message in body.get("messages", []) or []:
        if isinstance(message, dict) and message.get("role") == "user":
            return conversation_key(_text_of(message.get("content")))
    return ""
