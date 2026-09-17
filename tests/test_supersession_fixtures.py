"""Hand-authored supersession micro-fixtures (CI hard gate when flag on)."""
from __future__ import annotations

import pytest

from memor.embed.fake import FakeEmbedder
from memor.retrieve.retriever import Retriever
from memor.retrieve.similarity import cosine_to_stored_sim
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact, Scope

# (stale_text, replacement_text, query) — replacement must win / stale drop when both candidates.
FIXTURES = [
    ("we use React 17 for the UI", "we upgraded to React 18 for the UI", "which react version"),
    ("Redis is our session cache", "we switched from Redis to Memcached for sessions", "session cache"),
    ("REST for internal APIs", "migrated from REST to gRPC for internal APIs", "internal api transport"),
    ("MongoDB stores user profiles", "migrated from MongoDB to PostgreSQL for profiles", "user profile storage"),
    ("webpack builds the frontend", "replaced webpack with esbuild for frontend builds", "frontend bundler"),
    ("JWT auth in the API gateway", "we no longer use JWT; sessions are cookie-based", "api authentication"),
    ("Python 3.9 is the runtime", "we moved to Python 3.11 as the runtime", "python version"),
    ("Heroku hosts staging", "staging moved away from Heroku to Fly.io", "staging host"),
    ("CircleCI runs CI", "replaced CircleCI with GitHub Actions for CI", "ci system"),
    ("the default branch is master", "default branch changed to main", "default git branch"),
]


@pytest.fixture
def supersession_on(monkeypatch):
    monkeypatch.setenv("MEMOR_SUPERSESSION", "1")


@pytest.mark.parametrize("stale,replacement,query", FIXTURES)
def test_co_retrieved_stale_is_dropped(tmp_path, supersession_on, monkeypatch, stale, replacement, query):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    e = FakeEmbedder(dim=16)
    o = Artifact(id="stale", kind="memory", project="p", source="t",
                 text=stale, token_count=10, created_at=1.0,
                 meta={"mem_type": "decision"})
    m = Artifact(id="fresh", kind="memory", project="p", source="t",
                 text=replacement, token_count=10, created_at=2.0,
                 meta={"mem_type": "decision"})
    s.add_artifacts([o, m], e.embed([stale, replacement]))
    s.add_dispute("stale", "fresh")
    s.recompute_validity("stale")
    band = cosine_to_stored_sim(0.85)
    monkeypatch.setattr(
        s, "search",
        lambda *a, **k: [(o, band), (m, band)],
    )
    monkeypatch.setattr(s, "search_lexical", lambda *a, **k: [])
    r = Retriever(s, e, k=8, min_similarity=-1.0, memory_lane=0.0, edge_expand=False)
    ids = {h.artifact.id for h in r.query(query, Scope(project="p")).hits}
    assert "fresh" in ids
    assert "stale" not in ids
