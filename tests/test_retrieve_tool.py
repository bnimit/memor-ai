"""The marker promises an original; this tool is what keeps the promise.

Without a declared tool, `[memor:ccr:abc123]` tells the model that full content
exists and gives it no way to ask. That gap is the reason the source guard
cannot be lifted: a shortened file the model cannot restore is precisely the
failure the guard exists to prevent.

The tests are mostly about restraint. A tool declaration is not free -- it costs
tokens on every request and sits in the prefix a provider caches -- so it must
appear only when a marker is present, and must never disturb tools that are
already there.
"""
import pytest

from memor.proxy.pipeline import CCR_MARKER_PREFIX
from memor.proxy.retrieve_tool import (
    TOOL_NAME,
    body_has_marker,
    inject_retrieve_tool,
)


def _tools():
    return [{"name": "Read", "input_schema": {}},
            {"name": "Edit", "input_schema": {}}]


def _body(tools=None, messages=None):
    return {
        "tools": _tools() if tools is None else tools,
        "messages": messages or [{"role": "user", "content": "hello"}],
    }


def _tool_result(text):
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": text}]}


class TestWhenItFires:
    def test_declared_when_a_marker_is_present(self):
        out = inject_retrieve_tool("anthropic", _body(), has_marker=True)
        assert [t["name"] for t in out["tools"]][-1] == TOOL_NAME

    def test_not_declared_without_a_marker(self):
        """Otherwise every request pays for an affordance it cannot use."""
        body = _body()
        out = inject_retrieve_tool("anthropic", body, has_marker=False)
        assert out is body
        assert TOOL_NAME not in [t["name"] for t in out["tools"]]

    def test_not_declared_twice(self):
        body = _body(tools=_tools() + [{"name": TOOL_NAME, "input_schema": {}}])
        out = inject_retrieve_tool("anthropic", body, has_marker=True)
        assert [t.get("name") for t in out["tools"]].count(TOOL_NAME) == 1

    def test_a_request_with_no_tools_is_left_alone(self):
        """An agent sending no tools cannot call one; inventing an array would
        change the request shape rather than extend it."""
        body = {"messages": [], "tools": []}
        assert inject_retrieve_tool("anthropic", body, has_marker=True) is body
        body2 = {"messages": []}
        assert inject_retrieve_tool("anthropic", body2, has_marker=True) is body2


class TestCacheSafety:
    def test_existing_tools_keep_their_order(self):
        """Tool order is part of the cached prefix. Reordering it turns cache
        reads into writes, which costs more than compression saves."""
        out = inject_retrieve_tool("anthropic", _body(), has_marker=True)
        assert [t["name"] for t in out["tools"][:2]] == ["Read", "Edit"]

    def test_ours_is_appended_last(self):
        out = inject_retrieve_tool("anthropic", _body(), has_marker=True)
        assert out["tools"][-1]["name"] == TOOL_NAME

    def test_the_original_body_is_not_mutated(self):
        body = _body()
        before = list(body["tools"])
        inject_retrieve_tool("anthropic", body, has_marker=True)
        assert body["tools"] == before


class TestProviderShapes:
    def test_anthropic_uses_input_schema(self):
        out = inject_retrieve_tool("anthropic", _body(), has_marker=True)
        tool = out["tools"][-1]
        assert "input_schema" in tool and tool["input_schema"]["required"] == ["id"]

    def test_openai_uses_the_function_wrapper(self):
        body = {"tools": [{"type": "function", "function": {"name": "Read"}}],
                "messages": []}
        out = inject_retrieve_tool("openai", body, has_marker=True)
        tool = out["tools"][-1]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == TOOL_NAME
        assert "parameters" in tool["function"]

    def test_an_openai_declaration_is_recognised_as_already_present(self):
        body = {"tools": [{"type": "function", "function": {"name": TOOL_NAME}}],
                "messages": []}
        out = inject_retrieve_tool("openai", body, has_marker=True)
        assert len(out["tools"]) == 1


class TestMarkerDetection:
    def test_a_marker_in_a_tool_result_is_found(self):
        body = _body(messages=[_tool_result(f"{CCR_MARKER_PREFIX}abc]\nshortened")])
        assert body_has_marker(body, CCR_MARKER_PREFIX)

    def test_a_marker_in_block_form_content_is_found(self):
        body = _body(messages=[{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": [
                {"type": "text", "text": f"{CCR_MARKER_PREFIX}xyz]\nbody"}]}]}])
        assert body_has_marker(body, CCR_MARKER_PREFIX)

    def test_an_openai_tool_message_is_found(self):
        body = {"messages": [
            {"role": "tool", "content": f"{CCR_MARKER_PREFIX}q]\nshortened"}]}
        assert body_has_marker(body, CCR_MARKER_PREFIX)

    def test_prose_mentioning_a_marker_is_not_a_payload(self):
        """The model discussing compression is not a payload needing retrieval."""
        body = _body(messages=[{"role": "assistant", "content": [
            {"type": "text",
             "text": f"I noticed a {CCR_MARKER_PREFIX}abc] marker in the output"}]}])
        assert not body_has_marker(body, CCR_MARKER_PREFIX)

    def test_a_clean_request_has_no_marker(self):
        assert not body_has_marker(_body(messages=[_tool_result("plain output")]),
                                   CCR_MARKER_PREFIX)


class TestDescription:
    def test_the_description_names_the_trigger(self):
        """A tool the model never thinks to call is the same as no tool."""
        from memor.proxy.retrieve_tool import TOOL_DESCRIPTION

        text = TOOL_DESCRIPTION.lower()
        assert "memor:ccr" in text, "must show the marker the model will see"
        assert "editing" in text or "edit" in text, "must name the case that matters"
