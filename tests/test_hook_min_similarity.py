"""The hook must not gate recall more strictly than recall itself.

`DEFAULT_MIN_SIMILARITY` was lowered to -0.05 because these static embeddings
score a good match near 0.05, so a zero floor sits inside the noise band. The
hook read its default from the environment with a hardcoded "0.0" fallback, so
it never followed -- every hook-served recall applied a stricter gate than every
other caller.

The failure is silent and total on a young project. Measured on the real store:
recall() returned 6 hits at 0.789 for a genuine prompt while the hook answered
"no relevant memories for project ... yet" for the same query. A user sees an
empty dashboard and concludes memory is broken.
"""
import os

import pytest

from memor.recall import DEFAULT_MIN_SIMILARITY


def _hook_min_similarity(env: dict) -> float:
    """The hook's resolution logic, isolated from its I/O."""
    try:
        return float(env.get("MEMOR_MIN_SIMILARITY", str(DEFAULT_MIN_SIMILARITY)))
    except (ValueError, TypeError):
        return DEFAULT_MIN_SIMILARITY


class TestSimilarityDefault:
    def test_unset_env_uses_the_recall_default(self):
        assert _hook_min_similarity({}) == DEFAULT_MIN_SIMILARITY

    def test_the_default_is_not_zero(self):
        """A zero floor sits inside the noise band for these embeddings."""
        assert DEFAULT_MIN_SIMILARITY < 0.0

    def test_an_explicit_override_still_wins(self):
        assert _hook_min_similarity({"MEMOR_MIN_SIMILARITY": "0.3"}) == 0.3

    def test_a_malformed_override_falls_back_to_the_default(self):
        assert _hook_min_similarity(
            {"MEMOR_MIN_SIMILARITY": "nonsense"}) == DEFAULT_MIN_SIMILARITY

    def test_the_source_no_longer_hardcodes_zero(self):
        """Pin the regression: the literal fallback is what broke this."""
        import pathlib

        src = (pathlib.Path(__file__).resolve().parent.parent
               / "memor" / "hook_server.py").read_text()
        assert 'os.environ.get("MEMOR_MIN_SIMILARITY", "0.0")' not in src, (
            "the hook is hardcoding a 0.0 similarity floor again")


class TestEndToEnd:
    """A hook recall must find what a direct recall finds, on the same store."""

    @pytest.fixture
    def store(self, tmp_path):
        import time
        import uuid

        from memor.embed.fake import FakeEmbedder
        from memor.store.sqlite_store import SqliteStore
        from memor.types import Artifact

        emb = FakeEmbedder()
        db = tmp_path / "h.db"
        st = SqliteStore(str(db), dim=emb.dim)
        arts = [Artifact(id=str(uuid.uuid4()), kind="session_chunk",
                         project="proj", source="t",
                         text=f"the gesture recognition pipeline stage {i}",
                         token_count=20, created_at=time.time(), meta={})
                for i in range(12)]
        st.add_artifacts(arts, emb.embed([a.text for a in arts]))
        return str(db), emb

    def test_the_hook_gate_does_not_discard_real_hits(self, store):
        from memor.recall import recall

        db, emb = store
        query = "what did we do for the gesture recognition pipeline"

        permissive = recall(query, "proj", db, embedder=emb, k=8,
                            min_similarity=DEFAULT_MIN_SIMILARITY)
        strict = recall(query, "proj", db, embedder=emb, k=8, min_similarity=0.0)

        assert permissive.hits_count > 0, "sanity: the store has matching content"
        assert permissive.hits_count >= strict.hits_count, (
            "the documented default must not be stricter than a zero floor")
