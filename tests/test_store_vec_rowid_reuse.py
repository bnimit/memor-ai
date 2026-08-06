"""Re-adding an artifact whose row was deleted must not fail on the vec index.

``vec_artifacts`` is a separate virtual table keyed by rowid, with no foreign
key back to ``artifacts``. SQLite reuses the rowid of a deleted artifact, so a
new artifact can be handed a rowid whose embedding is still present. The insert
then violates the primary key and the whole ingest is lost -- which is exactly
what silently dropped every jcode ingest.
"""
from __future__ import annotations

import time

from memor.embed.fake import FakeEmbedder
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact


def _artifact(aid, text="some remembered text"):
    return Artifact(
        id=aid, kind="session_chunk", project="p", source="test",
        text=text, token_count=3, created_at=time.time(), meta={},
    )


def _store(tmp_path):
    embedder = FakeEmbedder(dim=16)
    return SqliteStore(str(tmp_path / "t.db"), dim=embedder.dim), embedder


def test_reinsert_after_raw_delete_does_not_raise(tmp_path):
    """The real failure: rows deleted straight from `artifacts` orphan vectors."""
    store, embedder = _store(tmp_path)
    arts = [_artifact(f"a{i}", f"text number {i}") for i in range(5)]
    store.add_artifacts(arts, embedder.embed([a.text for a in arts]))

    # Delete the artifacts without touching the vec index, as a maintenance
    # pass or a manual cleanup would.
    store.db.execute("DELETE FROM artifacts")
    store.db.commit()

    # Re-adding must succeed, not fail on a leftover embedding.
    store.add_artifacts(arts, embedder.embed([a.text for a in arts]))
    n = store.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
    assert n == 5


def test_updating_an_existing_artifact_still_works(tmp_path):
    """The ordinary overwrite path must be unaffected."""
    store, embedder = _store(tmp_path)
    a = _artifact("a1", "the original text")
    store.add_artifacts([a], embedder.embed([a.text]))

    updated = _artifact("a1", "the revised text")
    store.add_artifacts([updated], embedder.embed([updated.text]))

    rows = store.db.execute("SELECT text FROM artifacts WHERE id='a1'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "the revised text"


def test_vec_rows_do_not_outnumber_artifacts(tmp_path):
    """One embedding per artifact, however many times they are rewritten."""
    store, embedder = _store(tmp_path)
    arts = [_artifact(f"a{i}") for i in range(4)]
    for _ in range(3):
        store.add_artifacts(arts, embedder.embed([a.text for a in arts]))

    n_art = store.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
    n_vec = store.db.execute("SELECT COUNT(*) FROM vec_artifacts").fetchone()[0]
    assert n_art == 4
    assert n_vec == n_art
