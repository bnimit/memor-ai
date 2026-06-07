"""Tests for session context window — enriching sparse queries with recent history."""
from memor.session_context import SessionContextWindow


def test_empty_window_returns_query_unchanged():
    w = SessionContextWindow(max_queries=3)
    assert w.enrich("fix the bug", "sess1") == "fix the bug"


def test_window_stores_and_enriches():
    w = SessionContextWindow(max_queries=3)
    w.record("sess1", "refactor the auth module to use OAuth2")
    w.record("sess1", "also update the middleware to check token expiry")
    enriched = w.enrich("try the other approach", "sess1")
    assert "auth" in enriched.lower() or "OAuth2" in enriched
    assert "try the other approach" in enriched


def test_window_isolates_sessions():
    w = SessionContextWindow(max_queries=3)
    w.record("sess1", "refactor the auth module")
    enriched = w.enrich("do it", "sess2")
    # sess2 has no history, so no enrichment
    assert enriched == "do it"


def test_window_caps_at_max_queries():
    w = SessionContextWindow(max_queries=2)
    w.record("s1", "first query about databases")
    w.record("s1", "second query about auth tokens")
    w.record("s1", "third query about caching layer")
    enriched = w.enrich("continue", "s1")
    # first query should have been evicted
    assert "databases" not in enriched.lower()
    assert "caching" in enriched.lower()


def test_window_evicts_old_sessions():
    w = SessionContextWindow(max_queries=3, max_sessions=2)
    w.record("s1", "auth module refactor")
    w.record("s2", "database migration")
    w.record("s3", "caching layer")  # evicts s1
    enriched_s1 = w.enrich("continue", "s1")
    assert enriched_s1 == "continue"  # s1 was evicted


def test_enrich_does_not_duplicate_long_query():
    """A sufficiently complex query should not be padded with context."""
    w = SessionContextWindow(max_queries=3)
    w.record("s1", "refactor auth")
    long_query = "implement the full OAuth2 flow with PKCE and refresh tokens in the auth handler"
    enriched = w.enrich(long_query, "s1")
    assert enriched == long_query  # complex query doesn't need enrichment


def test_enrich_adds_context_for_sparse_query():
    w = SessionContextWindow(max_queries=3)
    w.record("s1", "implement the OAuth2 PKCE flow in auth/handler.py")
    sparse = "yes do it"
    enriched = w.enrich(sparse, "s1")
    assert "OAuth2" in enriched or "auth" in enriched.lower()
