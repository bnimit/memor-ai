"""Distilled memories get a lane; transcripts that quote the query get demoted.

Both changes exist because of one measurement on the real store: memories are
9.3% of active artifacts but reached only 3.0% of injected hits. The cause was
not embedding quality (cosine to real prompts 0.275 vs 0.273 for chunks), not
index coverage (100% both), and not distillation (still running daily). It was
competition -- 35% of top hits repeated more than half the query verbatim,
because a prompt drawn from a live session is near-duplicate text to the chunk
that recorded it, and a paraphrase cannot beat a copy of the question.

These tests pin the properties that make the fix safe rather than merely
effective: k is never padded, an empty lane costs nothing, and a short query
cannot be mistaken for an echo.
"""
import time
import uuid

import pytest

from memor.retrieve.retriever import (
    SELF_RECALL_MIN_QUERY_CHARS,
    Retriever,
    _containment,
    select_with_memory_lane,
)
from memor.types import Artifact, Hit, Scope


def _art(kind: str, text: str, aid: str = None) -> Artifact:
    return Artifact(id=aid or str(uuid.uuid4()), kind=kind, project="p",
                    source="t", text=text, token_count=10,
                    created_at=time.time(), meta={})


def _hit(kind: str, score: float, text: str = "x") -> Hit:
    return Hit(_art(kind, text), score, {})


class TestMemoryLane:
    def test_memories_are_promoted_into_reserved_slots(self):
        ranked = [_hit("session_chunk", 1.0 - i / 100) for i in range(20)]
        ranked += [_hit("memory", 0.1)]
        out = select_with_memory_lane(ranked, k=8, share=0.25)
        assert len(out) == 8
        assert sum(1 for h in out if h.artifact.kind == "memory") == 1

    def test_k_is_never_padded_when_no_memories_exist(self):
        """An empty lane returns its slots to the general pool."""
        ranked = [_hit("session_chunk", 1.0 - i / 100) for i in range(20)]
        out = select_with_memory_lane(ranked, k=8, share=0.25)
        assert len(out) == 8
        assert all(h.artifact.kind == "session_chunk" for h in out)

    def test_result_count_never_exceeds_k(self):
        ranked = [_hit("memory", 0.9) for _ in range(20)]
        assert len(select_with_memory_lane(ranked, k=8, share=0.25)) == 8

    def test_fewer_candidates_than_k_is_not_inflated(self):
        ranked = [_hit("session_chunk", 0.9), _hit("memory", 0.5)]
        assert len(select_with_memory_lane(ranked, k=8, share=0.25)) == 2

    def test_memories_winning_on_score_do_not_displace_extra_slots(self):
        """The lane is a floor, not a quota: it must not force a second copy."""
        ranked = [_hit("memory", 1.0), _hit("memory", 0.99)]
        ranked += [_hit("session_chunk", 0.5 - i / 100) for i in range(10)]
        out = select_with_memory_lane(ranked, k=8, share=0.25)
        assert sum(1 for h in out if h.artifact.kind == "memory") == 2

    def test_share_zero_is_a_true_escape_hatch(self):
        ranked = [_hit("session_chunk", 1.0 - i / 100) for i in range(10)]
        ranked += [_hit("memory", 0.01)]
        out = select_with_memory_lane(ranked, k=8, share=0.0)
        assert all(h.artifact.kind == "session_chunk" for h in out)


class TestSelfRecallContainment:
    def test_a_chunk_quoting_the_query_is_detected(self):
        q = "can we not automate the entire process where a thin proxy sits in front of the agent"
        assert _containment(q, f"user: {q} and then some more discussion") > 0.9

    def test_a_short_query_is_never_treated_as_an_echo(self):
        """"auth refresh" is contained in any relevant document.

        Containment on a short query measures relevance, not echo. An existing
        retriever test caught this: two equally-matching artifacts both scored
        1.0 containment, were demoted together, and the recency tie-break the
        test asserted was silently inverted.
        """
        short = "auth refresh"
        assert len(short) < SELF_RECALL_MIN_QUERY_CHARS
        assert _containment(short, "auth refresh token loop") == 0.0

    def test_a_paraphrase_is_not_an_echo(self):
        q = "why did we choose the proxy approach for compression instead of a wrapper library"
        assert _containment(q, "We chose the proxy because it needs no code changes.") < 0.5

    def test_empty_inputs_are_safe(self):
        assert _containment("", "x") == 0.0
        assert _containment("x" * 100, "") == 0.0


class TestRetrieverIntegration:
    @pytest.fixture
    def store(self, tmp_path):
        from memor.embed.fake import FakeEmbedder
        from memor.store.sqlite_store import SqliteStore

        emb = FakeEmbedder()
        st = SqliteStore(str(tmp_path / "t.db"), dim=emb.dim)
        arts, texts = [], []
        for i in range(40):
            arts.append(_art("session_chunk", f"deploy pipeline retry notes {i}"))
            texts.append(arts[-1].text)
        for i in range(5):
            arts.append(_art("memory", f"decision: deploy pipeline retries {i}"))
            texts.append(arts[-1].text)
        st.add_artifacts(arts, emb.embed(texts))
        return st, emb

    def test_lane_raises_memory_share_without_changing_result_count(self, store):
        st, emb = store
        q = "what did we decide about the deploy pipeline retry behaviour overall"
        off = Retriever(st, emb, k=8, memory_lane=0.0).query(q, Scope(project="p"))
        on = Retriever(st, emb, k=8, memory_lane=0.25).query(q, Scope(project="p"))
        assert len(on.hits) == len(off.hits) == 8
        n_off = sum(1 for h in off.hits if h.artifact.kind == "memory")
        n_on = sum(1 for h in on.hits if h.artifact.kind == "memory")
        assert n_on >= n_off

    def test_suppression_can_be_disabled(self, store):
        st, emb = store
        q = "deploy pipeline retry notes 7 and what happened next in the run"
        r = Retriever(st, emb, k=8, suppress_self_recall=False).query(q, Scope(project="p"))
        assert not any(h.components.get("self_recall") for h in r.hits)


class TestLaneSearch:
    """The lane runs its own KNN; re-ranking the general pool is not enough.

    Measured on the real store, memories are 4.2% of the dense top-8 and 4.1% of
    the top-32, so widening the pool does not surface them -- there is usually no
    memory present to promote. A dedicated kind-restricted search found
    candidates for 38 of 60 real queries that the general pool never saw.
    """

    @pytest.fixture
    def store(self, tmp_path):
        from memor.embed.fake import FakeEmbedder
        from memor.store.sqlite_store import SqliteStore

        emb = FakeEmbedder()
        st = SqliteStore(str(tmp_path / "t.db"), dim=emb.dim)
        arts, texts = [], []
        # Overwhelm the pool so no memory can reach the top-k on its own.
        for i in range(200):
            arts.append(_art("session_chunk", f"deploy pipeline retry log line {i}"))
            texts.append(arts[-1].text)
        arts.append(_art("memory", "decision: the deploy pipeline retries twice then alerts",
                         aid="the-memory"))
        texts.append(arts[-1].text)
        st.add_artifacts(arts, emb.embed(texts))
        return st, emb

    def test_lane_queries_the_store_for_memories_directly(self, store):
        """The lane must issue its own kind-restricted search.

        Asserting "the memory is absent without the lane" is not portable --
        with the deterministic FakeEmbedder the memory may legitimately win on
        score. What is invariant is that the lane asks the store for memories,
        which is the behaviour that lets it find candidates the general pool
        never returned on the real store.
        """
        st, emb = store
        seen_kinds = []
        real_search = st.search

        def spy(vec, scope, k):
            seen_kinds.append(tuple(scope.kinds) if scope.kinds else None)
            return real_search(vec, scope, k)

        st.search = spy
        try:
            Retriever(st, emb, k=8, memory_lane=0.25).query(
                "how does the deploy pipeline retry", Scope(project="p"))
        finally:
            st.search = real_search
        assert ("memory",) in seen_kinds, (
            f"no memory-restricted search was issued; saw {seen_kinds}")

    def test_no_lane_search_when_disabled(self, store):
        st, emb = store
        seen = []
        real_search = st.search

        def spy(vec, scope, k):
            seen.append(tuple(scope.kinds) if scope.kinds else None)
            return real_search(vec, scope, k)

        st.search = spy
        try:
            Retriever(st, emb, k=8, memory_lane=0.0).query(
                "how does the deploy pipeline retry", Scope(project="p"))
        finally:
            st.search = real_search
        assert ("memory",) not in seen

    def test_lane_does_not_change_the_number_of_results(self, store):
        st, emb = store
        q = "what is the decision about how the deploy pipeline retries before alerting"
        off = Retriever(st, emb, k=8, memory_lane=0.0).query(q, Scope(project="p"))
        on = Retriever(st, emb, k=8, memory_lane=0.25).query(q, Scope(project="p"))
        assert len(on.hits) == len(off.hits)

    def test_an_irrelevant_memory_is_not_admitted(self, tmp_path):
        """The floor exists so the lane cannot buy share with padding."""
        from memor.embed.fake import FakeEmbedder
        from memor.store.sqlite_store import SqliteStore

        emb = FakeEmbedder()
        st = SqliteStore(str(tmp_path / "u.db"), dim=emb.dim)
        arts, texts = [], []
        for i in range(50):
            arts.append(_art("session_chunk", f"kubernetes ingress certificate rotation {i}"))
            texts.append(arts[-1].text)
        arts.append(_art("memory", "unrelated: the office coffee machine needs descaling"))
        texts.append(arts[-1].text)
        st.add_artifacts(arts, emb.embed(texts))
        r = Retriever(st, emb, k=8, memory_lane=0.25).query(
            "kubernetes ingress certificate rotation failing on renewal", Scope(project="p"))
        assert not any(h.artifact.kind == "memory" for h in r.hits), (
            "an off-topic memory must not be promoted just to fill the lane")
