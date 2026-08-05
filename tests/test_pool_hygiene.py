"""Keeping the retrieval pool free of noise, duplicates, and redundant results.

Three faults, all measured on a real 26,297-artifact store:

* 488 harness artifacts — 150 copies of the interrupt marker, 40-53 copies each
  of subagent prompt headers — that ingestion had no rule against;
* 5,975 duplicate rows in 2,581 groups (22.7%), because ingestion deduplicates
  within a session and never across them;
* redundancy-blind top-k, so a live recall spent three of five slots on one
  identical text and the caller received three distinct memories, not five.
"""
from __future__ import annotations

import pytest

from memor.ingest.claude_code import _signal_score
from memor.prune import content_key, find_duplicates, find_noise, prune
from memor.retrieve.retriever import MMR_LAMBDA, mmr_select
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact


# --- A: the ingestion filter --------------------------------------------------


@pytest.mark.parametrize("text", [
    "[Request interrupted by user for tool use]",
    "[Tool use was rejected by the user]",
    "API Error: connection reset by peer while streaming the response",
    "## Adversarial Claim Verifier (voter 3/3)\n\nBe SKEPTICAL. Try to refute.",
    "### Code Reviewer (reviewer 2/4)\n\nReview the diff for correctness issues.",
])
def test_harness_noise_is_rejected(text):
    assert _signal_score(text, "assistant", 50) == 0.0


@pytest.mark.parametrize("text", [
    "We decided to use argon2 for password hashing instead of bcrypt, "
    "because the memory-hardness matters more than raw speed here.",
    "The root cause was a stale cache key: the request id was being reused "
    "across retries, so the second attempt read the first attempt's result.",
])
def test_real_content_still_survives(text):
    assert _signal_score(text, "assistant", 50) > 0.0


def test_a_voter_shaped_heading_inside_real_prose_is_not_dropped():
    """The rule matches a brief's opening line, not a mention of one."""
    text = ("We reviewed the output of the verifier pass. ## Adversarial Claim "
            "Verifier (voter 1/3) produced a false negative, so we decided to "
            "raise the threshold.")
    assert _signal_score(text, "assistant", 60) > 0.0


# --- B: duplicates ------------------------------------------------------------


def _art(aid, text, project="p", created=100.0):
    return Artifact(id=aid, kind="session_chunk", project=project, source="s",
                    text=text, token_count=20, created_at=created, meta={})


@pytest.fixture
def store(tmp_path):
    return SqliteStore(str(tmp_path / "p.db"), dim=16)


class _Emb:
    dim = 16

    def embed(self, texts):
        return [[float(len(t) % 7)] * 16 for t in texts]


def test_whitespace_differences_are_the_same_content():
    assert content_key("a  b\nc") == content_key("a b c")


def test_different_content_gets_different_keys():
    assert content_key("the queue uses redis") != content_key("the queue uses postgres")


def test_duplicates_are_found_and_the_earliest_is_kept(store):
    e = _Emb()
    arts = [_art("first", "same text here", created=100.0),
            _art("second", "same text here", created=200.0),
            _art("third", "same text here", created=300.0)]
    store.add_artifacts(arts, e.embed([a.text for a in arts]))

    losers = find_duplicates(store)
    assert set(losers) == {"second", "third"}, "the earliest copy must survive"


def test_the_same_text_in_another_project_is_not_a_duplicate(store):
    e = _Emb()
    arts = [_art("a", "shared text", project="one"),
            _art("b", "shared text", project="two")]
    store.add_artifacts(arts, e.embed([a.text for a in arts]))
    assert find_duplicates(store) == []


def test_noise_already_stored_is_found(store):
    e = _Emb()
    arts = [_art("n1", "[Request interrupted by user for tool use]"),
            _art("k1", "we decided to use argon2 for password hashing")]
    store.add_artifacts(arts, e.embed([a.text for a in arts]))
    assert find_noise(store) == ["n1"]


def test_a_dry_run_changes_nothing(store):
    e = _Emb()
    arts = [_art("a", "same"), _art("b", "same")]
    store.add_artifacts(arts, e.embed([a.text for a in arts]))
    before = prune(store, dry_run=True)
    assert before["duplicates"] == 1
    assert store.db.execute(
        "SELECT COUNT(*) c FROM artifacts WHERE active=1").fetchone()["c"] == 2


def test_applying_deactivates_rather_than_deletes(store):
    """Recall history points at these ids; they must stay resolvable."""
    e = _Emb()
    arts = [_art("keep", "same", created=1.0), _art("drop", "same", created=2.0)]
    store.add_artifacts(arts, e.embed([a.text for a in arts]))
    prune(store, dry_run=False)

    row = store.db.execute("SELECT active FROM artifacts WHERE id='drop'").fetchone()
    assert row is not None, "the row was deleted, not retired"
    assert row["active"] == 0
    assert store.db.execute(
        "SELECT active FROM artifacts WHERE id='keep'").fetchone()["active"] == 1


def test_pruning_twice_is_stable(store):
    e = _Emb()
    arts = [_art("a", "same", created=1.0), _art("b", "same", created=2.0)]
    store.add_artifacts(arts, e.embed([a.text for a in arts]))
    prune(store, dry_run=False)
    second = prune(store, dry_run=False)
    assert second["total"] == 0


# --- C: redundancy-aware selection --------------------------------------------


class _Hit:
    def __init__(self, aid, score):
        self.artifact = type("A", (), {"id": aid})()
        self.score = score


def test_identical_candidates_cannot_fill_every_slot():
    """The live failure: three of five slots on one text."""
    same = [1.0, 0.0]
    hits = [_Hit("a", 0.9), _Hit("b", 0.89), _Hit("c", 0.88), _Hit("d", 0.5)]
    vectors = {"a": same, "b": same, "c": same, "d": [0.0, 1.0]}
    picked = [h.artifact.id for h in mmr_select(hits, vectors, 2)]
    assert picked[0] == "a", "the most relevant must still come first"
    assert "d" in picked, "a distinct memory must beat a third copy"


def test_relevance_still_leads_when_nothing_is_redundant():
    hits = [_Hit("a", 0.9), _Hit("b", 0.7), _Hit("c", 0.5)]
    vectors = {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [0.7, 0.7]}
    assert [h.artifact.id for h in mmr_select(hits, vectors, 3)][0] == "a"


def test_candidates_without_vectors_are_not_dropped():
    hits = [_Hit("a", 0.9), _Hit("b", 0.8)]
    picked = [h.artifact.id for h in mmr_select(hits, {}, 2)]
    assert set(picked) == {"a", "b"}


def test_it_never_returns_more_than_asked_for():
    hits = [_Hit(str(i), 1.0 - i / 10) for i in range(9)]
    assert len(mmr_select(hits, {}, 3)) == 3


def test_a_short_list_passes_through():
    assert len(mmr_select([_Hit("a", 1.0)], {}, 5)) == 1
    assert mmr_select([], {}, 5) == []


def test_lambda_one_is_the_old_behaviour():
    """Pure relevance: a safety valve if diversity ever misbehaves."""
    same = [1.0, 0.0]
    hits = [_Hit("a", 0.9), _Hit("b", 0.89), _Hit("c", 0.2)]
    vectors = {"a": same, "b": same, "c": [0.0, 1.0]}
    picked = [h.artifact.id for h in mmr_select(hits, vectors, 2, lam=1.0)]
    assert picked == ["a", "b"]
    assert MMR_LAMBDA < 1.0, "the shipped default must actually diversify"


# --- the store can supply what MMR needs --------------------------------------


def test_vectors_are_retrievable_for_selection(store):
    e = _Emb()
    arts = [_art("a", "alpha text"), _art("b", "beta text")]
    store.add_artifacts(arts, e.embed([a.text for a in arts]))
    vectors = store.vectors_for(["a", "b"])
    assert set(vectors) == {"a", "b"}
    assert len(vectors["a"]) == 16


def test_asking_for_nothing_is_safe(store):
    assert store.vectors_for([]) == {}
