from memor.hook_server import handle_request, IDLE_TIMEOUT_S
from memor.query_complexity import _TRIVIAL_PATTERNS
from memor.embed.fake import FakeEmbedder
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact


def test_handle_request_returns_json(tmp_path):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(id="a1", kind="memory", project="myproj", source="distill",
                   text="we decided to use argon2 for password hashing in the auth module",
                   token_count=12, created_at=100.0,
                   meta={"mem_type": "decision", "session_id": "s1"})
    s.add_artifacts([art], e.embed([art.text]))

    req = {"prompt": "password hashing", "cwd": str(tmp_path / "myproj"), "session_id": "test"}
    result = handle_request(req, db_path=db_path, embedder=e)
    assert "hookSpecificOutput" in result
    output = result["hookSpecificOutput"]
    assert output["hookEventName"] == "UserPromptSubmit"
    assert "additionalContext" in output


def test_handle_request_empty_db(tmp_path):
    db_path = str(tmp_path / "nope.db")
    e = FakeEmbedder(dim=16)
    req = {"prompt": "how does the auth module handle password hashing?",
           "cwd": str(tmp_path / "proj"), "session_id": "test"}
    result = handle_request(req, db_path=db_path, embedder=e)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "empty" in ctx.lower() or "daemon" in ctx.lower()


def test_handle_request_no_embedder(tmp_path):
    req = {"prompt": "test", "cwd": str(tmp_path), "session_id": "s1"}
    result = handle_request(req, db_path=str(tmp_path / "nope.db"), embedder=None)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "inactive" in ctx.lower() or "OPENAI_API_KEY" in ctx


def test_handle_request_skips_trivial_prompt(tmp_path):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    SqliteStore(db_path, dim=16)
    req = {"prompt": "yes", "cwd": str(tmp_path / "proj"), "session_id": "trivial-test-1"}
    result = handle_request(req, db_path=db_path, embedder=e)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "skipped" in ctx.lower()


def test_handle_request_skips_trivial_with_punctuation(tmp_path):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    SqliteStore(db_path, dim=16)
    req = {"prompt": "looks good!", "cwd": str(tmp_path / "proj"), "session_id": "trivial-test-2"}
    result = handle_request(req, db_path=db_path, embedder=e)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "skipped" in ctx.lower()


def test_handle_request_does_not_skip_real_query(tmp_path):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(id="a1", kind="memory", project="myproj", source="distill",
                   text="we decided to use argon2 for password hashing in the auth module",
                   token_count=12, created_at=100.0,
                   meta={"mem_type": "decision", "session_id": "s1"})
    s.add_artifacts([art], e.embed([art.text]))
    req = {"prompt": "how does password hashing work in the auth module?",
           "cwd": str(tmp_path / "myproj"), "session_id": "test"}
    result = handle_request(req, db_path=db_path, embedder=e)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "skipped" not in ctx.lower()


def test_session_injected_tracking(tmp_path):
    import memor.hook_server as hs
    hs._session_injected.clear()
    hs._session_injected.setdefault("sess-a", set()).add("m1")
    hs._session_injected.setdefault("sess-a", set()).add("m2")
    assert "m1" in hs._session_injected["sess-a"]
    assert "m2" in hs._session_injected["sess-a"]
    assert "sess-b" not in hs._session_injected
    hs._session_injected.clear()


def test_trivial_patterns_are_lowercase():
    for p in _TRIVIAL_PATTERNS:
        assert p == p.lower(), f"Pattern '{p}' should be lowercase"


def test_idle_timeout_is_set():
    assert IDLE_TIMEOUT_S == 600
