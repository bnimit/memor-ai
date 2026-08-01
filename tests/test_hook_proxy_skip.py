"""Tests for hook skip when an agent is proxied."""
import memor.config as cfg
from memor.embed.fake import FakeEmbedder
from memor.hook_server import handle_request
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact


def _patch_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)


def _make_db(tmp_path):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="a1", kind="memory", project="proj", source="distill",
        text="use argon2 for password hashing", token_count=6,
        created_at=100.0, meta={"mem_type": "decision", "session_id": "old"},
    )
    s.add_artifacts([art], e.embed([art.text]))
    return db_path, e, s


def test_hook_skips_inject_when_goose_proxied(tmp_path, monkeypatch):
    _patch_config(tmp_path, monkeypatch)
    cfg.set_proxy_agent("goose", True)

    db_path, e, s = _make_db(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    req = {
        "event": "UserPromptSubmit",
        "session_id": "g1",
        "message": "how does password hashing work in our authentication flow?",
    }
    resp = handle_request(req, db_path=db_path, embedder=e)

    ctx = resp["hookSpecificOutput"]["additionalContext"]
    assert ctx == ""
    assert s.db.execute("SELECT COUNT(*) AS n FROM recall_log").fetchone()["n"] == 0


def test_hook_injects_when_goose_not_proxied(tmp_path, monkeypatch):
    _patch_config(tmp_path, monkeypatch)
    cfg.set_proxy_agent("goose", False)

    db_path, e, s = _make_db(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    req = {
        "event": "UserPromptSubmit",
        "session_id": "g1",
        "message": "how does password hashing work in our authentication flow?",
    }
    resp = handle_request(req, db_path=db_path, embedder=e)

    ctx = resp["hookSpecificOutput"]["additionalContext"]
    assert ctx
    assert "Recalled Memories" in ctx or "no relevant" in ctx.lower()
    row = s.db.execute(
        "SELECT agent, status FROM recall_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["agent"] == "goose"
    assert row["status"] != "skipped_proxy"
