from memor.hook_server import handle_request, IDLE_TIMEOUT_S
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
    req = {"prompt": "anything", "cwd": str(tmp_path / "proj"), "session_id": "test"}
    result = handle_request(req, db_path=db_path, embedder=e)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "empty" in ctx.lower() or "daemon" in ctx.lower()


def test_handle_request_no_embedder(tmp_path):
    req = {"prompt": "test", "cwd": str(tmp_path), "session_id": "s1"}
    result = handle_request(req, db_path=str(tmp_path / "nope.db"), embedder=None)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "inactive" in ctx.lower() or "OPENAI_API_KEY" in ctx


def test_idle_timeout_is_set():
    assert IDLE_TIMEOUT_S == 600
