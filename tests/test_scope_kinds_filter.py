"""`scope.kinds` must select the top-k OF THAT KIND, not filter the top-k.

The implementation sliced to k first and filtered after, which asks a different
question: "of the global top k, which happen to be this kind". For a minority
kind the answer is usually none. On the real store, asking for 3 memories
returned 0 while asking for 200 returned 13 -- same query, same corpus, so the
shortfall was the slice.

This matters beyond the API contract: distilled memories are ~9% of artifacts
and were reaching only 3% of injected hits, and any per-kind retrieval lane
built to correct that would have silently returned nothing.
"""
import time
import uuid

import pytest

from memor.types import Artifact, Scope


def _artifact(kind: str, text: str, project: str = "p") -> Artifact:
    return Artifact(
        id=str(uuid.uuid4()), kind=kind, project=project, source="test",
        text=text, token_count=max(len(text) // 4, 1), created_at=time.time(),
        meta={},
    )


@pytest.fixture
def store(tmp_path):
    from memor.embed.fake import FakeEmbedder
    from memor.store.sqlite_store import SqliteStore

    emb = FakeEmbedder()
    st = SqliteStore(str(tmp_path / "t.db"), dim=emb.dim)
    arts, texts = [], []
    # A realistic imbalance: the minority kind is outnumbered ~9:1, as in the
    # real store, and every artifact is on-topic so ranking alone decides.
    for i in range(90):
        arts.append(_artifact("session_chunk", f"deploy pipeline notes number {i}"))
        texts.append(arts[-1].text)
    for i in range(10):
        arts.append(_artifact("memory", f"deploy pipeline decision number {i}"))
        texts.append(arts[-1].text)
    st.add_artifacts(arts, emb.embed(texts))
    return st, emb


def test_kind_filter_returns_k_of_that_kind(store):
    st, emb = store
    qv = emb.embed(["deploy pipeline"])[0]
    for k in (1, 3, 5, 10):
        got = st.search(qv, Scope(project="p", kinds={"memory"}), k)
        assert len(got) == min(k, 10), (
            f"asked for {k} memories, got {len(got)}; the kind filter is being "
            "applied after truncation")
        assert all(a.kind == "memory" for a, _ in got)


def test_small_k_is_not_starved_by_a_majority_kind(store):
    """The original bug in its sharpest form: small k returned nothing."""
    st, emb = store
    qv = emb.embed(["deploy pipeline"])[0]
    got = st.search(qv, Scope(project="p", kinds={"memory"}), 3)
    assert got, "a minority kind must still be retrievable at small k"


def test_unfiltered_search_is_unchanged(store):
    """The fix must not alter the default path, which has no kinds filter."""
    st, emb = store
    qv = emb.embed(["deploy pipeline"])[0]
    got = st.search(qv, Scope(project="p"), 8)
    assert len(got) == 8


def test_multi_kind_filter_selects_from_all_named_kinds(store):
    st, emb = store
    qv = emb.embed(["deploy pipeline"])[0]
    got = st.search(qv, Scope(project="p", kinds={"memory", "session_chunk"}), 12)
    assert len(got) == 12
    assert {a.kind for a, _ in got} <= {"memory", "session_chunk"}


def test_results_stay_ordered_by_similarity(store):
    """Filtering before truncation must not disturb ranking order."""
    st, emb = store
    qv = emb.embed(["deploy pipeline"])[0]
    sims = [s for _, s in st.search(qv, Scope(project="p", kinds={"memory"}), 10)]
    assert sims == sorted(sims, reverse=True)


def test_absent_kind_returns_empty_not_wrong_kind(store):
    st, emb = store
    qv = emb.embed(["deploy pipeline"])[0]
    assert st.search(qv, Scope(project="p", kinds={"nonexistent"}), 5) == []
