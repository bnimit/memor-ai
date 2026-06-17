"""Tests for ingest-time reaffirmation (memor/distill/reaffirm.py)."""
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact
from memor.distill.reaffirm import reaffirm_from_chunks


def _mem(id, text, created):
    return Artifact(id=id, kind="memory", project="p", source="distill",
                    text=text, token_count=len(text.split()), created_at=created, meta={})


def _chunk(id, text, created):
    return Artifact(id=id, kind="session_chunk", project="p", source="t",
                    text=text, token_count=len(text.split()), created_at=created, meta={})


def _store(tmp_path):
    e = FakeEmbedder(dim=32)
    s = SqliteStore(str(tmp_path / "m.db"), dim=32)
    return s, e


def test_matching_chunk_reaffirms_memory(tmp_path):
    s, e = _store(tmp_path)
    s.add_artifacts([_mem("m1", "use argon2 for password hashing", 100.0)],
                    e.embed(["use argon2 for password hashing"]))
    chunks = [_chunk("c1", "use argon2 for password hashing", 500.0)]
    n = reaffirm_from_chunks(s, chunks, e.embed([c.text for c in chunks]))
    assert n == 1
    assert s.get_reaffirmed_timestamps(["m1"])["m1"] == 500.0


def test_cue_chunk_does_not_reaffirm(tmp_path):
    s, e = _store(tmp_path)
    s.add_artifacts([_mem("m1", "use argon2 for password hashing", 100.0)],
                    e.embed(["use argon2 for password hashing"]))
    # Same topic but a replacement cue -> a contradiction, not a reaffirmation.
    chunks = [_chunk("c1", "use argon2 for password hashing instead of bcrypt", 500.0)]
    n = reaffirm_from_chunks(s, chunks, e.embed([c.text for c in chunks]))
    assert n == 0
    assert "m1" not in s.get_reaffirmed_timestamps(["m1"])


def test_dissimilar_chunk_does_not_reaffirm(tmp_path):
    s, e = _store(tmp_path)
    s.add_artifacts([_mem("m1", "use argon2 for password hashing", 100.0)],
                    e.embed(["use argon2 for password hashing"]))
    chunks = [_chunk("c1", "the dashboard renders a bar chart of recalls", 500.0)]
    n = reaffirm_from_chunks(s, chunks, e.embed([c.text for c in chunks]))
    assert n == 0


def test_reaffirm_takes_latest_matching_chunk_time(tmp_path):
    s, e = _store(tmp_path)
    s.add_artifacts([_mem("m1", "use argon2 for password hashing", 100.0)],
                    e.embed(["use argon2 for password hashing"]))
    chunks = [_chunk("c1", "use argon2 for password hashing", 500.0),
              _chunk("c2", "use argon2 for password hashing", 800.0)]
    reaffirm_from_chunks(s, chunks, e.embed([c.text for c in chunks]))
    assert s.get_reaffirmed_timestamps(["m1"])["m1"] == 800.0
