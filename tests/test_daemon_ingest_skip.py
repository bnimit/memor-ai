"""Re-ingest must not re-embed chunks the store already has."""
from __future__ import annotations

import json
from pathlib import Path

from memor.daemon import ingest_unit
from memor.embed.fake import FakeEmbedder
from memor.ingest.sources import IngestUnit
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact


def _chunk(aid: str, text: str, project="p"):
    return Artifact(
        id=aid, kind="session_chunk", project=project, source="test",
        text=text, token_count=5, created_at=1.0,
        meta={"session_id": "s1", "ord": 0},
    )


def test_reingest_skips_embed_for_existing_ids(tmp_path, monkeypatch):
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    embedder = FakeEmbedder(dim=16)
    existing = _chunk("c1", "first turn about auth middleware")
    store.add_artifacts([existing], embedder.embed([existing.text]))

    calls = {"n": 0}
    real_embed = embedder.embed

    def counting_embed(texts):
        calls["n"] += 1
        return real_embed(texts)

    embedder.embed = counting_embed

    arts = [
        existing,
        _chunk("c2", "second turn about token refresh"),
    ]

    unit = IngestUnit(
        state_key="s",
        mtime=1.0,
        project="p",
        agent="test",
        parse=lambda: arts,
        path=None,
    )
    n = ingest_unit(unit, store, embedder)
    assert n == 1  # only the new chunk
    assert calls["n"] == 1
    # Only one text embedded (the new one)
    assert store.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 2


def test_reingest_all_existing_returns_zero_and_does_not_embed(tmp_path):
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    embedder = FakeEmbedder(dim=16)
    arts = [_chunk("c1", "only chunk")]
    store.add_artifacts(arts, embedder.embed([a.text for a in arts]))

    calls = {"n": 0}
    real_embed = embedder.embed

    def counting_embed(texts):
        calls["n"] += 1
        return real_embed(texts)

    embedder.embed = counting_embed
    unit = IngestUnit(
        state_key="s", mtime=2.0, project="p", agent="test",
        parse=lambda: arts, path=None,
    )
    assert ingest_unit(unit, store, embedder) == 0
    assert calls["n"] == 0


def test_poll_defers_excess_units(tmp_path, monkeypatch):
    from memor import daemon
    from memor.daemon import run_poll_cycle
    import os

    monkeypatch.setattr(daemon, "MAX_UNITS_PER_POLL", 2)
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    embedder = FakeEmbedder(dim=16)
    proj = tmp_path / "projects" / "-Users-x-p"
    proj.mkdir(parents=True)
    base = 1_700_000_000.0
    for i in range(5):
        p = proj / f"s{i}.jsonl"
        p.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-01T10:00:00Z",
                "message": {"role": "user", "content": f"fix auth bug number {i} in login"},
            }) + "\n"
        )
        os.utime(p, (base + i, base + i))

    state = {}
    state, _, _ = run_poll_cycle(state, store, embedder, tmp_path / "projects")
    assert len(state) == 2
    state, _, _ = run_poll_cycle(state, store, embedder, tmp_path / "projects")
    assert len(state) == 4
    state, _, _ = run_poll_cycle(state, store, embedder, tmp_path / "projects")
    assert len(state) == 5