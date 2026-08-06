"""The proxy must scope and inject with no memor-specific headers.

Real Claude Code only points ANTHROPIC_BASE_URL at the proxy; it never sends
x-memor-project or x-agent. If scoping depended on those, every production
recall would fall back to the global bucket and return almost nothing, while
any test that sets them would still pass.
"""
from __future__ import annotations

from memor.proxy.memory import _content_to_text
from memor.proxy.scope import project_from_body, resolve_request_project

PROJ = "/Users/nimit/Documents/Projects/Memorable"


def _body(last_user_content):
    return {
        "system": [{"type": "text",
                    "text": f"You are Claude Code.\n\nWorking directory: {PROJ}\n"}],
        "messages": [
            {"role": "user", "content": "read the ingest module"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Read",
                 "input": {"file_path": f"{PROJ}/memor/ingest/jcode.py"}}]},
            {"role": "user", "content": last_user_content},
        ],
    }


def test_project_resolves_from_the_declared_working_directory():
    """No header, so the system prompt's working directory has to carry it."""
    assert resolve_request_project("", _body("what did we learn?")) == "Memorable"


def test_project_resolves_from_touched_file_paths_alone():
    """Even without a declared directory, the files being read locate the work."""
    body = {"messages": [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": f"{PROJ}/memor/daemon.py"}}]},
        {"role": "user", "content": "why is it slow"},
    ]}
    assert project_from_body(body) == "Memorable"


def test_a_body_with_no_evidence_is_undeterminable():
    """Nothing to go on must read as unknown, not as a wrong guess."""
    body = {"messages": [{"role": "user", "content": "hello"}]}
    assert project_from_body(body) is None
    assert resolve_request_project("", body) == "unknown"


def test_a_tool_result_is_not_a_query():
    """A turn ending in a tool_result has no question to retrieve on.

    This is why a realistic-looking request can legitimately inject nothing:
    the last user message carries tool output, not words.
    """
    assert _content_to_text(
        [{"type": "tool_result", "tool_use_id": "t1", "content": "some output"}]
    ).strip() == ""


def test_a_real_question_after_tool_use_still_yields_a_query():
    assert "what did we learn" in _content_to_text(
        _body("what did we learn about jcode hooks")["messages"][-1]["content"])
