import json
from memor.hook_server import handle_request
from memor.embed.fake import FakeEmbedder
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact


def test_hook_outputs_valid_json(tmp_path):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(id="a1", kind="memory", project="myproj", source="distill",
                   text="we decided to use argon2 for password hashing in the auth module",
                   token_count=12, created_at=100.0,
                   meta={"mem_type": "decision", "session_id": "s1"})
    s.add_artifacts([art], e.embed([art.text]))

    req = {"prompt": "password hashing", "cwd": str(tmp_path / "myproj"),
           "session_id": "test-session"}
    result = handle_request(req, db_path=db_path, embedder=e)
    output = json.dumps(result)
    parsed = json.loads(output)
    assert "hookSpecificOutput" in parsed
    assert parsed["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert isinstance(parsed["hookSpecificOutput"]["additionalContext"], str)


def test_hook_graceful_on_missing_db(tmp_path):
    e = FakeEmbedder(dim=16)
    req = {"prompt": "test", "cwd": str(tmp_path), "session_id": "s1"}
    result = handle_request(req, db_path=str(tmp_path / "nope.db"), embedder=e)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "Memor:" in ctx


def test_hook_no_embedder_status(tmp_path):
    req = {"prompt": "test", "cwd": str(tmp_path), "session_id": "s1"}
    result = handle_request(req, db_path=str(tmp_path / "nope.db"), embedder=None)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "inactive" in ctx.lower() or "OPENAI_API_KEY" in ctx
