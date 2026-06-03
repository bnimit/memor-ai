"""Tests for the daemon state tracking and polling logic (not the infinite loop)."""
from __future__ import annotations

import json
import time
from pathlib import Path

from memorable.daemon import (
    _project_name_from_dir,
    load_state,
    save_state,
    scan_transcripts,
    run_poll_cycle,
    ingest_file,
)


# -- project name extraction --------------------------------------------------

def test_project_name_simple():
    assert _project_name_from_dir("-Users-nimit-Documents-Projects-plirin") == "plirin"

def test_project_name_nested():
    assert _project_name_from_dir("-Users-nimit-Documents-Eukarya-reearth-flow") == "flow"

def test_project_name_worktree():
    assert _project_name_from_dir(
        "-Users-nimit-Documents-Eukarya-ygo--claude-worktrees-musing-haibt-701a57"
    ) == "701a57"

def test_project_name_passthrough():
    assert _project_name_from_dir("simple") == "simple"


# -- state persistence ---------------------------------------------------------

def test_load_state_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("memorable.daemon.STATE_FILE", tmp_path / "nonexistent.json")
    assert load_state() == {}

def test_save_and_load_state(tmp_path, monkeypatch):
    state_file = tmp_path / "state" / "ingested.json"
    state_dir = tmp_path / "state"
    monkeypatch.setattr("memorable.daemon.STATE_FILE", state_file)
    monkeypatch.setattr("memorable.daemon.STATE_DIR", state_dir)
    data = {"/some/path.jsonl": 1717430000.0}
    save_state(data)
    assert state_file.exists()
    monkeypatch.setattr("memorable.daemon.STATE_FILE", state_file)
    assert load_state() == data

def test_load_state_corrupt(tmp_path, monkeypatch):
    state_file = tmp_path / "ingested.json"
    state_file.write_text("not json{{{")
    monkeypatch.setattr("memorable.daemon.STATE_FILE", state_file)
    assert load_state() == {}


# -- scan_transcripts ----------------------------------------------------------

def test_scan_transcripts(tmp_path):
    # Set up a fake projects dir
    proj_dir = tmp_path / "-Users-nimit-Documents-Projects-myproj"
    proj_dir.mkdir()
    (proj_dir / "abc.jsonl").write_text('{"type":"user","timestamp":"2026-05-01T10:00:00Z","message":{"role":"user","content":"hello"}}\n')
    (proj_dir / "def.jsonl").write_text('{"type":"user","timestamp":"2026-05-01T10:00:00Z","message":{"role":"user","content":"world"}}\n')
    # Non-jsonl should be ignored
    (proj_dir / "memory").mkdir()

    results = scan_transcripts(tmp_path)
    assert len(results) == 2
    paths = [str(p) for p, _ in results]
    projects = [proj for _, proj in results]
    assert all(proj == "myproj" for proj in projects)
    assert any("abc.jsonl" in p for p in paths)
    assert any("def.jsonl" in p for p in paths)

def test_scan_transcripts_empty(tmp_path):
    assert scan_transcripts(tmp_path / "nonexistent") == []


# -- ingest_file ---------------------------------------------------------------

def test_ingest_file(tmp_path):
    from memorable.embed.fake import FakeEmbedder
    from memorable.store.sqlite_store import SqliteStore

    db = str(tmp_path / "test.db")
    embedder = FakeEmbedder(dim=16)
    store = SqliteStore(db, dim=embedder.dim)

    transcript = tmp_path / "sess1.jsonl"
    transcript.write_text(
        '{"type":"user","timestamp":"2026-05-01T10:00:00Z","message":{"role":"user","content":"fix the auth refresh loop in the login handler"}}\n'
        '{"type":"assistant","timestamp":"2026-05-01T10:00:05Z","message":{"role":"assistant","content":[{"type":"text","text":"The loop is caused by re-issuing the token on 401 without checking the retry count. Here is the fix."}]}}\n'
    )

    count = ingest_file(transcript, "testproj", store, embedder)
    assert count == 2


# -- run_poll_cycle ------------------------------------------------------------

def test_poll_cycle_ingests_new_files(tmp_path):
    from memorable.embed.fake import FakeEmbedder
    from memorable.store.sqlite_store import SqliteStore

    db = str(tmp_path / "test.db")
    embedder = FakeEmbedder(dim=16)
    store = SqliteStore(db, dim=embedder.dim)

    proj_dir = tmp_path / "projects" / "-Users-x-myproj"
    proj_dir.mkdir(parents=True)
    t = proj_dir / "sess1.jsonl"
    t.write_text(
        '{"type":"user","timestamp":"2026-05-01T10:00:00Z","message":{"role":"user","content":"fix the auth refresh loop in the login handler"}}\n'
    )

    state = {}
    state = run_poll_cycle(state, store, embedder, tmp_path / "projects")
    assert str(t) in state
    assert state[str(t)] == t.stat().st_mtime

def test_poll_cycle_skips_already_ingested(tmp_path):
    from memorable.embed.fake import FakeEmbedder
    from memorable.store.sqlite_store import SqliteStore

    db = str(tmp_path / "test.db")
    embedder = FakeEmbedder(dim=16)
    store = SqliteStore(db, dim=embedder.dim)

    proj_dir = tmp_path / "projects" / "-Users-x-myproj"
    proj_dir.mkdir(parents=True)
    t = proj_dir / "sess1.jsonl"
    t.write_text(
        '{"type":"user","timestamp":"2026-05-01T10:00:00Z","message":{"role":"user","content":"fix the auth refresh loop in the login handler"}}\n'
    )

    # Pre-populate state with current mtime
    state = {str(t): t.stat().st_mtime}
    state_after = run_poll_cycle(state, store, embedder, tmp_path / "projects")
    # State should be unchanged (file was skipped)
    assert state_after == state

def test_poll_cycle_reingests_modified_file(tmp_path):
    from memorable.embed.fake import FakeEmbedder
    from memorable.store.sqlite_store import SqliteStore

    db = str(tmp_path / "test.db")
    embedder = FakeEmbedder(dim=16)
    store = SqliteStore(db, dim=embedder.dim)

    proj_dir = tmp_path / "projects" / "-Users-x-myproj"
    proj_dir.mkdir(parents=True)
    t = proj_dir / "sess1.jsonl"
    t.write_text(
        '{"type":"user","timestamp":"2026-05-01T10:00:00Z","message":{"role":"user","content":"fix the auth refresh loop in the login handler"}}\n'
    )

    # Pre-populate state with an older mtime
    state = {str(t): t.stat().st_mtime - 100}
    state_after = run_poll_cycle(state, store, embedder, tmp_path / "projects")
    # State should now reflect the current mtime
    assert state_after[str(t)] == t.stat().st_mtime

def test_poll_cycle_handles_bad_file(tmp_path):
    from memorable.embed.fake import FakeEmbedder
    from memorable.store.sqlite_store import SqliteStore

    db = str(tmp_path / "test.db")
    embedder = FakeEmbedder(dim=16)
    store = SqliteStore(db, dim=embedder.dim)

    proj_dir = tmp_path / "projects" / "-Users-x-myproj"
    proj_dir.mkdir(parents=True)
    bad = proj_dir / "bad.jsonl"
    bad.write_text("not valid json at all\n")
    good = proj_dir / "good.jsonl"
    good.write_text(
        '{"type":"user","timestamp":"2026-05-01T10:00:00Z","message":{"role":"user","content":"fix the auth refresh loop in the login handler"}}\n'
    )

    state = {}
    state = run_poll_cycle(state, store, embedder, tmp_path / "projects")
    # Good file should be in state, bad file should not (error -> no state update)
    assert str(good) in state
    assert str(bad) not in state
