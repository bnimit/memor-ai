"""The dense floor must not sit inside the noise band.

min_similarity gates dense candidates on raw cosine. It was 0.0, on the
reasoning that relevant content scores above zero and noise below. These static
embeddings do not have that margin: a good match scores about 0.05, so a floor
at 0.0 rejected genuine matches by hundredths and half of all realistic queries
against a well-populated project returned nothing at all.
"""
from __future__ import annotations

import time

from memor.embed.fake import FakeEmbedder
from memor.recall import DEFAULT_MIN_SIMILARITY
from memor.retrieve.retriever import MIN_SIMILARITY_FLOOR, Retriever
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact, Scope


def test_floor_is_below_zero():
    """A zero floor discards matches that these embeddings score just under."""
    assert DEFAULT_MIN_SIMILARITY < 0.0
    assert DEFAULT_MIN_SIMILARITY >= -0.1, \
        "below -0.1 the measured precision cost stops being noise"


def test_retriever_default_matches_recall_default():
    """A Retriever built directly must not gate more strictly than recall().

    The eval harness and the CLI construct Retriever themselves; if the two
    defaults drift, they measure a different system than the one users get.
    """
    assert MIN_SIMILARITY_FLOOR == DEFAULT_MIN_SIMILARITY


def test_a_slightly_negative_candidate_is_kept(tmp_path):
    """The behaviour the old floor got wrong, pinned end to end."""
    embedder = FakeEmbedder(dim=16)
    store = SqliteStore(str(tmp_path / "t.db"), dim=embedder.dim)
    arts = [
        Artifact(id=f"a{i}", kind="session_chunk", project="p", source="t",
                 text=f"a memory about topic {i} with distinct wording",
                 token_count=5, created_at=time.time(), meta={})
        for i in range(8)
    ]
    store.add_artifacts(arts, embedder.embed([a.text for a in arts]))

    # A retriever on the shipped default must return something for a query
    # drawn from the stored text.
    r = Retriever(store, embedder, k=5)
    trace = r.query("a memory about topic 3 with distinct wording", Scope(project="p"))
    assert trace.hits, "the default floor rejected an exact-text query"


def test_the_gate_can_still_be_disabled(tmp_path):
    """min_similarity below -1.0 remains the documented escape hatch."""
    embedder = FakeEmbedder(dim=16)
    store = SqliteStore(str(tmp_path / "t2.db"), dim=embedder.dim)
    a = Artifact(id="a1", kind="session_chunk", project="p", source="t",
                 text="something entirely unrelated", token_count=3,
                 created_at=time.time(), meta={})
    store.add_artifacts([a], embedder.embed([a.text]))

    r = Retriever(store, embedder, k=5, min_similarity=-2.0)
    assert r.query("a completely different question", Scope(project="p")).hits


def test_the_floor_still_rejects_an_unrelated_query(tmp_path):
    """The point of a floor is keeping junk out of the agent's context.

    Lowering it is only safe if a question the store cannot answer still
    returns nothing. Measured on the real store, -0.05 answered 9 of 10
    relevant questions with 0 of 8 irrelevant ones; -0.1 answered 10 of 10 but
    started admitting junk, which is why the floor stops where it does.
    """
    embedder = FakeEmbedder(dim=16)
    store = SqliteStore(str(tmp_path / "t3.db"), dim=embedder.dim)
    arts = [
        Artifact(id=f"a{i}", kind="session_chunk", project="p", source="t",
                 text=f"database migration rollback procedure step {i}",
                 token_count=6, created_at=time.time(), meta={})
        for i in range(6)
    ]
    store.add_artifacts(arts, embedder.embed([a.text for a in arts]))

    r = Retriever(store, embedder, k=5)
    hits = r.query("sourdough bread proofing temperature", Scope(project="p")).hits
    # Not a hard zero -- a tiny fake-embedding store can coincide -- but the
    # gate must not simply pass everything through.
    assert len(hits) < len(arts), "the floor admitted the entire store"
