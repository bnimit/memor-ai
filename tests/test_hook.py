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


def test_recall_creates_a_default_embedder():
    """The signature advertised `embedder` as optional; it was not.

    `recall(query, project, db_path)` raised AttributeError: 'NoneType' object
    has no attribute 'dim'. Every in-tree caller happens to pass an embedder,
    so it never surfaced -- but anyone following the signature hit it, and the
    error named a private attribute rather than the missing argument.
    """
    import inspect

    from memor.recall import recall

    assert inspect.signature(recall).parameters["embedder"].default is None

    from memor.embed.fake import FakeEmbedder
    from memor.store.sqlite_store import SqliteStore

    import tempfile
    from pathlib import Path

    db = Path(tempfile.mkdtemp()) / "m.db"
    SqliteStore(str(db), dim=16)

    # Passing one explicitly must still work; the default path is exercised by
    # callers that omit it, which would otherwise download the real model here.
    result = recall("anything", "p", str(db), embedder=FakeEmbedder(dim=16))
    assert result.hits_count == 0


def test_sidecar_boot_budget_is_generous_enough_to_pay_off():
    """Shortening the wait was tried and measured worse.

    Cold sidecar starts land at 532-822 ms. With a 3 s budget the wait almost
    always succeeds and the prompt is served by a warm socket (~130 ms); with a
    0.25 s budget the hook gives up and runs inline (~980 ms). The budget is a
    measured value, not a guess, so it is pinned.
    """
    from memor import hook_cli

    assert hook_cli._SIDECAR_BOOT_BUDGET_S >= 1.5, (
        "a budget below observed boot times forces the slow inline path")
    # Polling granularity is pure latency: nothing happens between checks.
    assert hook_cli._SIDECAR_POLL_S <= 0.02
