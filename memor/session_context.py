"""Session context window — tracks recent queries per session to enrich
sparse follow-up prompts with conversational context for retrieval.

When a user types "try the other approach" after discussing OAuth2, the
retriever gets the enriched query "implement OAuth2 PKCE flow ... try the
other approach" instead of searching on 5 bare words.
"""
from __future__ import annotations

from collections import OrderedDict

from memor.query_complexity import score_query

_SPARSE_THRESHOLD = 0.35


class SessionContextWindow:
    def __init__(self, *, max_queries: int = 5, max_sessions: int = 50):
        self._max_queries = max_queries
        self._max_sessions = max_sessions
        self._windows: OrderedDict[str, list[str]] = OrderedDict()

    def record(self, session_id: str, query: str) -> None:
        if session_id not in self._windows:
            if len(self._windows) >= self._max_sessions:
                self._windows.popitem(last=False)
            self._windows[session_id] = []
        else:
            self._windows.move_to_end(session_id)
        buf = self._windows[session_id]
        buf.append(query)
        if len(buf) > self._max_queries:
            del buf[0]

    def enrich(self, query: str, session_id: str) -> str:
        if score_query(query) >= _SPARSE_THRESHOLD:
            return query
        history = self._windows.get(session_id)
        if not history:
            return query
        context = " | ".join(history[-2:])
        return f"{context} | {query}"
