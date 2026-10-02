"""Recall-time soft supersession behind MEMOR_SUPERSESSION."""
from __future__ import annotations

from memor.embed.fake import FakeEmbedder
from memor.retrieve.retriever import Retriever
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact, Scope


def _seed(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    e = FakeEmbedder(dim=16)
    old = Artifact(
        id="o", kind="memory", project="p", source="t",
        text="we use redis for caching", token_count=5,
        created_at=1.0, meta={"mem_type": "decision"},
    )
    new = Artifact(
        id="m", kind="memory", project="p", source="t",
        text="we use postgres for caching", token_count=5,
        created_at=2.0, meta={"mem_type": "decision"},
    )
    s.add_artifacts([old, new], e.embed([old.text, new.text]))
    s.add_dispute("o", "m")
    s.recompute_validity("o")
    return s, e


def test_flag_off_keeps_both(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMOR_SUPERSESSION", raising=False)
    s, e = _seed(tmp_path)
    # Force both into candidates via generous k / low gate
    r = Retriever(s, e, k=8, min_similarity=-1.0, memory_lane=0.0)
    # Patch search to return both with high sim so they enter the set
    arts = [
        s._row_to_artifact(s.db.execute("SELECT * FROM artifacts WHERE id='o'").fetchone()),
        s._row_to_artifact(s.db.execute("SELECT * FROM artifacts WHERE id='m'").fetchone()),
    ]
    monkeypatch.setattr(s, "search", lambda *a, **k: [(arts[0], 0.9), (arts[1], 0.9)])
    monkeypatch.setattr(s, "search_lexical", lambda *a, **k: [])
    trace = r.query("caching", Scope(project="p"))
    ids = {h.artifact.id for h in trace.hits}
    assert "o" in ids and "m" in ids


def test_flag_on_drops_disputed_when_disputer_present(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOR_SUPERSESSION", "1")
    s, e = _seed(tmp_path)
    arts = [
        s._row_to_artifact(s.db.execute("SELECT * FROM artifacts WHERE id='o'").fetchone()),
        s._row_to_artifact(s.db.execute("SELECT * FROM artifacts WHERE id='m'").fetchone()),
    ]
    monkeypatch.setattr(s, "search", lambda *a, **k: [(arts[0], 0.9), (arts[1], 0.9)])
    monkeypatch.setattr(s, "search_lexical", lambda *a, **k: [])
    r = Retriever(s, e, k=8, min_similarity=-1.0, memory_lane=0.0, edge_expand=False)
    trace = r.query("caching", Scope(project="p"))
    ids = {h.artifact.id for h in trace.hits}
    assert "m" in ids
    assert "o" not in ids
