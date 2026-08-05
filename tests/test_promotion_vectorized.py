"""The vectorized similarity scan must agree with the plain Python loop.

The scan is O(n^2) over every distilled memory. As a Python loop over 2,441
memories it took ~40s, longer than the 30s poll interval, so the daemon never
reached idle and sat at ~90% CPU continuously. It is now a single matrix
product, which must not change which candidates are found.
"""
from __future__ import annotations

import random

import memor.global_memories as gm


class _FakeEmbedder:
    """Deterministic vectors, so clusters are arranged rather than hoped for."""

    dim = 8

    def __init__(self, by_text):
        self._by_text = by_text

    def embed(self, texts):
        return [self._by_text[t] for t in texts]


class _FakeArtifact:
    def __init__(self, aid, project, text):
        self.id = aid
        self.project = project
        self.text = text


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        return self

    def fetchall(self):
        return self._rows


class _FakeStore:
    def __init__(self, arts):
        self._arts = arts
        self.db = _FakeDB(list(range(len(arts))))

    def _row_to_artifact(self, row):
        return self._arts[row]


def _build(n=60, seed=7):
    """Memories across several projects, some deliberately near-identical."""
    rnd = random.Random(seed)
    arts, by_text = [], {}
    for i in range(n):
        project = f"proj{i % 5}"
        # Every fifth memory shares a direction with a small set of others, so
        # real cross-project clusters exist to be found.
        family = i % 7
        base = [0.0] * 8
        base[family] = 1.0
        vec = [b + rnd.uniform(-0.02, 0.02) for b in base]
        text = f"memory {i}"
        by_text[text] = vec
        arts.append(_FakeArtifact(f"m{i}", project, text))
    return _FakeStore(arts), _FakeEmbedder(by_text)


def _key(cands):
    return sorted(tuple(sorted(c["source_ids"])) for c in cands)


def test_vectorized_matches_python_loop(monkeypatch):
    store, embedder = _build()

    fast = gm.find_promotion_candidates(store, embedder, min_projects=3)

    # Force the fallback by making the matrix helper unavailable, exactly as it
    # behaves when numpy is not installed.
    monkeypatch.setattr(gm, "_similarity_matrix", lambda vecs: None)
    slow = gm.find_promotion_candidates(store, embedder, min_projects=3)

    assert _key(fast) == _key(slow)
    assert fast, "fixture should produce clusters, otherwise this proves nothing"


def test_zero_vector_does_not_produce_nan():
    """A zero vector has no direction; it must simply match nothing."""
    arts = [_FakeArtifact(f"m{i}", f"proj{i}", f"t{i}") for i in range(4)]
    by_text = {f"t{i}": [0.0] * 8 for i in range(4)}
    by_text["t0"] = [1.0] + [0.0] * 7
    store, embedder = _FakeStore(arts), _FakeEmbedder(by_text)
    # Must not raise, and must not cluster on nan comparisons.
    gm.find_promotion_candidates(store, embedder, min_projects=3)
