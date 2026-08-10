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
    """Proxied agents defer to the proxy -- but only when it is really serving.

    The skip used to be unconditional. It is now conditional on the proxy
    having recorded a recall recently, because "configured" and "in the path"
    turned out to be different things: subscription Claude Code ignores
    ANTHROPIC_BASE_URL entirely. This test asserts the deferral, so it states
    the precondition explicitly.
    """
    from memor import hook_server

    monkeypatch.setattr(hook_server, "_proxy_serving_cache", None)
    monkeypatch.setattr(hook_server, "_proxy_is_serving_recall",
                        lambda *a, **k: True)
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


def test_hook_recalls_when_the_proxy_is_not_actually_serving(tmp_path, monkeypatch):
    """The skip was unconditional, and it cost the user their memory.

    `proxy_agents.claude = true` meant the hook returned empty context on every
    prompt, on the assumption the proxy would serve recall instead. But Claude
    Code authenticated with a subscription ignores ANTHROPIC_BASE_URL -- that
    variable is honoured for API-key users -- so the proxy never saw the
    conversation. On the development machine both ledgers were silent for four
    days while Claude Code logged thousands of records a day.
    """
    from memor import hook_server

    monkeypatch.setattr(hook_server, "_proxy_serving_cache", None)
    monkeypatch.setattr(hook_server, "_proxy_is_serving_recall",
                        lambda *a, **k: False)
    monkeypatch.setattr("memor.config.is_proxy_agent", lambda agent: True)

    # A real store, so the assertion is on observable output rather than on a
    # patched internal: handle_request imports recall locally, so patching the
    # module attribute would not be seen.
    db_path, embedder, store = _make_db(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    resp = hook_server.handle_request(
        {"event": "UserPromptSubmit", "session_id": "s1",
         "message": "how does password hashing work in our authentication flow?"},
        db_path=db_path, embedder=embedder)

    ctx = resp["hookSpecificOutput"]["additionalContext"]
    logged = store.db.execute(
        "SELECT COUNT(*) AS n FROM recall_log").fetchone()["n"]
    assert ctx != "", "the hook must inject when the proxy is not serving"
    assert logged == 1, "and the recall must be recorded"


def test_hook_still_defers_to_a_proxy_that_is_serving(tmp_path, monkeypatch):
    """When the proxy really is in the path, duplicating recall wastes tokens."""
    from memor import hook_server

    monkeypatch.setattr(hook_server, "_proxy_serving_cache", None)
    monkeypatch.setattr(hook_server, "_proxy_is_serving_recall",
                        lambda *a, **k: True)
    monkeypatch.setattr("memor.config.is_proxy_agent", lambda agent: True)

    resp = hook_server.handle_request({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "anything",
        "cwd": str(tmp_path),
        "session_id": "s",
    })
    assert resp["hookSpecificOutput"]["additionalContext"] == ""


def test_proxy_serving_check_fails_toward_injecting(tmp_path, monkeypatch):
    """An unanswerable question must not cost the user their memory.

    A duplicated recall costs some tokens; a skipped one costs the whole
    feature, silently.
    """
    from memor import hook_server

    monkeypatch.setattr(hook_server, "_proxy_serving_cache", None)
    assert hook_server._proxy_is_serving_recall(
        str(tmp_path / "does-not-exist.db")) is False


def test_proxy_serving_check_is_cached(tmp_path, monkeypatch):
    from memor import hook_server

    monkeypatch.setattr(hook_server, "_proxy_serving_cache", None)
    hook_server._proxy_is_serving_recall(str(tmp_path / "missing.db"))
    assert hook_server._proxy_serving_cache is not None
