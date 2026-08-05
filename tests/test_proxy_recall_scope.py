"""Which project a proxied request belongs to, and whether the recall is recorded.

Both halves of a four-day outage. The proxy asked for an ``x-memor-project``
header no client sends, so every request resolved to a project with zero
artifacts; and the proxy path never logged, so 4,056 empty recalls in a row
moved no number anywhere. Either fault alone is survivable. Together they are
silent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from memor.proxy.memory import inject_memory
from memor.proxy.scope import project_from_body, resolve_request_project
from memor.store.sqlite_store import SqliteStore

REPO = Path(__file__).resolve().parent.parent


def _tool_use(path: str) -> dict:
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t", "name": "Read", "input": {"file_path": path}}]}


# --- reading the project out of the request ----------------------------------


def test_a_declared_working_directory_is_used():
    body = {"system": f"You are an agent.\nWorking directory: {REPO}\n", "messages": []}
    assert project_from_body(body) == REPO.name


@pytest.mark.parametrize("label", ["Working directory", "cwd", "workspace", "Project root"])
def test_common_ways_of_stating_it_are_understood(label):
    body = {"system": f"{label}: {REPO}", "messages": []}
    assert project_from_body(body) == REPO.name


def test_a_system_prompt_given_as_blocks_is_read():
    body = {"system": [{"type": "text", "text": f"Working directory: {REPO}"}], "messages": []}
    assert project_from_body(body) == REPO.name


def test_touched_file_paths_are_enough_on_their_own():
    """No system prompt at all — the conversation still says where it is."""
    body = {"messages": [_tool_use(str(REPO / "memor" / "recall.py"))]}
    assert project_from_body(body) == REPO.name


def test_the_majority_of_paths_wins():
    """One read outside the tree must not move the scope."""
    body = {"messages": [
        _tool_use("/etc/hosts"),
        _tool_use(str(REPO / "memor" / "recall.py")),
        _tool_use(str(REPO / "memor" / "episodes.py")),
    ]}
    assert project_from_body(body) == REPO.name


def test_a_declared_directory_beats_scattered_paths():
    body = {
        "system": f"Working directory: {REPO}",
        "messages": [_tool_use("/tmp/somewhere/else.py")],
    }
    assert project_from_body(body) == REPO.name


# --- refusing to guess --------------------------------------------------------


def test_a_directory_that_does_not_exist_yields_nothing():
    body = {"system": "Working directory: /no/such/path/anywhere", "messages": []}
    assert project_from_body(body) is None


def test_a_path_outside_any_repository_yields_nothing():
    body = {"messages": [_tool_use("/etc/hosts")]}
    assert project_from_body(body) is None


def test_an_empty_body_yields_nothing():
    assert project_from_body({"messages": []}) is None
    assert project_from_body({}) is None
    assert project_from_body(None) is None


def test_malformed_content_does_not_raise():
    body = {"system": 42, "messages": [{"role": "user", "content": {"weird": True}}, "nope"]}
    assert project_from_body(body) is None


# --- the header still wins ----------------------------------------------------


def test_an_explicit_header_is_honoured():
    assert resolve_request_project(str(REPO), {"messages": []}) == REPO.name


def test_without_a_header_the_body_decides():
    body = {"messages": [_tool_use(str(REPO / "memor" / "recall.py"))]}
    assert resolve_request_project("", body) == REPO.name


def test_unknown_survives_when_there_is_no_evidence():
    """The regression: this used to be the answer for every single request."""
    assert resolve_request_project("", {"messages": []}) == "unknown"


# --- the recall is recorded ---------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return SqliteStore(str(tmp_path / "m.db"), dim=16)


class _Embedder:
    dim = 16

    def embed(self, texts):
        return [[0.0] * 16 for _ in texts]


def _recalls(store):
    return store.db.execute(
        "SELECT project, agent, hits_count, status FROM recall_log").fetchall()


def test_a_recall_that_finds_nothing_is_still_logged(store, tmp_path):
    """The event that was being discarded, and the only one that proves a fault."""
    db = str(tmp_path / "m.db")
    body = {"messages": [{"role": "user", "content": "does this project use pydantic"}]}
    inject_memory("anthropic", body, project="Memorable", db_path=db,
                  embedder=_Embedder(), store=store, agent="claude", session_id="s1")

    rows = _recalls(store)
    assert len(rows) == 1, "a zero-hit recall left no trace"
    assert rows[0]["hits_count"] == 0
    assert rows[0]["agent"] == "claude"
    assert rows[0]["project"] == "Memorable"


def test_logging_failure_never_costs_the_user_a_response(tmp_path):
    class Broken:
        def log_recall(self, **kw):
            raise RuntimeError("ledger down")

        def record_recall(self, ids):
            raise RuntimeError("ledger down")

    db = str(tmp_path / "m.db")
    body = {"messages": [{"role": "user", "content": "anything at all"}]}
    out = inject_memory("anthropic", body, project="p", db_path=db,
                        embedder=_Embedder(), store=Broken(), agent="claude")
    assert out is not None


def test_no_store_means_no_logging_and_no_crash(tmp_path):
    db = str(tmp_path / "m.db")
    body = {"messages": [{"role": "user", "content": "anything at all"}]}
    assert inject_memory("anthropic", body, project="p", db_path=db,
                         embedder=_Embedder()) is not None


def test_a_request_with_no_user_turn_is_not_a_recall(store, tmp_path):
    db = str(tmp_path / "m.db")
    inject_memory("anthropic", {"messages": [{"role": "assistant", "content": "hi"}]},
                  project="p", db_path=db, embedder=_Embedder(), store=store)
    assert _recalls(store) == []
