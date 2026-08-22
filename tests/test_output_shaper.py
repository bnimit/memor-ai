"""The output shaper must never touch a turn that could produce code.

The constraint is not "mostly safe": a truncated edit reaches shipped code, and
the saving is a few cents. So every gate here is a refusal test, and the module
fails closed on anything it does not recognise.

The numbers behind the design, measured on 120 real transcripts: 68% of output
characters are tool inputs (the code itself), 42% of prose sits inside
code-writing turns, and a naive "resuming after a tool result" trigger would
fire on 93% of assistant turns, 28% of which follow an error.
"""
import os

import pytest

from memor.proxy import output_shaper as osh


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv(osh.ENV_FLAG, "1")
    monkeypatch.setenv(osh.ENV_HOLDOUT, "0")


def _body(messages=None, system="You are a coding agent.", tools=None):
    body = {"messages": messages if messages is not None else
            [{"role": "user", "content": "what does this project do"}]}
    if system is not None:
        body["system"] = system
    if tools is not None:
        body["tools"] = tools
    return body


def _tool_use(name):
    return {"role": "assistant",
            "content": [{"type": "tool_use", "name": name, "input": {}}]}


def _tool_result(text):
    return {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t", "content": text}]}


class TestRefusals:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv(osh.ENV_FLAG, raising=False)
        assert osh.decide("anthropic", _body()).reason == "disabled"

    def test_a_conversation_that_edited_code_is_never_shaped(self):
        body = _body([{"role": "user", "content": "explain the retry logic"},
                      _tool_use("Edit"),
                      _tool_result("ok")])
        d = osh.decide("anthropic", body)
        assert not d.shaped and d.reason == "conversation_writes_code"

    @pytest.mark.parametrize("tool", sorted(osh.WRITE_TOOLS))
    def test_every_write_tool_suppresses_shaping(self, tool):
        body = _body([{"role": "user", "content": "look at this"}, _tool_use(tool)])
        assert not osh.decide("anthropic", body).shaped, tool

    @pytest.mark.parametrize("text", [
        "AssertionError: expected 3 got 4",
        "Traceback (most recent call last):",
        "FAILED tests/test_x.py::test_y",
        "error: cannot find module",
        "TypeError: undefined is not a function",
        "fatal: merge conflict in src/main.rs",
    ])
    def test_an_error_in_the_last_tool_result_suppresses_shaping(self, text):
        body = _body([{"role": "user", "content": "run the tests"},
                      _tool_use("Bash"), _tool_result(text)])
        d = osh.decide("anthropic", body)
        assert not d.shaped and d.reason == "error_in_tool_result", text

    def test_a_change_request_with_write_tools_available_is_not_shaped(self):
        body = _body([{"role": "user", "content": "fix the pagination bug"}],
                     tools=[{"name": "Edit"}, {"name": "Read"}])
        d = osh.decide("anthropic", body)
        assert not d.shaped and d.reason == "change_requested_with_write_tools"

    def test_an_unrecognised_system_shape_is_left_alone(self):
        body = _body(system=None)
        body["system"] = 12345          # not a string, not blocks
        out, d = osh.apply("anthropic", body)
        assert out is body and not d.shaped

    def test_empty_messages_are_not_shaped(self):
        assert not osh.decide("anthropic", _body([])).shaped


class TestSafeCases:
    def test_a_pure_question_is_shaped(self):
        out, d = osh.apply("anthropic", _body())
        assert d.shaped
        assert out["system"].endswith(osh.STEER_NOTE)

    def test_a_read_only_investigation_is_shaped(self):
        body = _body([{"role": "user", "content": "what does this module do"},
                      _tool_use("Read"), _tool_result("def main(): pass")])
        assert osh.decide("anthropic", body).shaped

    def test_the_note_forbids_abbreviating_code(self):
        """The instruction itself must not invite elision."""
        note = osh.STEER_NOTE.lower()
        assert "never abbreviate" in note
        assert "correctness comes before brevity" in note


class TestCacheSafety:
    def test_the_prefix_is_never_rewritten(self):
        """Rewriting the front turns cheap cache reads into full-price writes."""
        original = "You are a coding agent with a long cached preamble."
        out, _ = osh.apply("anthropic", _body(system=original))
        assert out["system"].startswith(original)

    def test_block_form_appends_to_the_last_text_block(self):
        body = _body(system=[{"type": "text", "text": "cached preamble"}])
        out, d = osh.apply("anthropic", body)
        assert d.shaped
        assert len(out["system"]) == 1, "must not add a block"
        assert out["system"][0]["text"].startswith("cached preamble")

    def test_the_original_body_is_not_mutated(self):
        body = _body()
        before = body["system"]
        osh.apply("anthropic", body)
        assert body["system"] == before

    def test_openai_shape_appends_to_the_system_message(self):
        body = {"messages": [{"role": "system", "content": "sys prompt"},
                             {"role": "user", "content": "explain this"}]}
        out, d = osh.apply("openai", body)
        assert d.shaped
        assert out["messages"][0]["content"].startswith("sys prompt")
        assert out["messages"][0]["content"].endswith(osh.STEER_NOTE)


class TestHoldout:
    def test_assignment_is_stable_for_a_conversation(self, monkeypatch):
        monkeypatch.setenv(osh.ENV_HOLDOUT, "0.5")
        first = osh.in_holdout("conversation-abc")
        for _ in range(20):
            assert osh.in_holdout("conversation-abc") is first

    def test_holdout_actually_splits_the_population(self, monkeypatch):
        monkeypatch.setenv(osh.ENV_HOLDOUT, "0.5")
        held = sum(osh.in_holdout(f"conv-{i}") for i in range(400))
        assert 150 < held < 250, f"expected ~200 of 400, got {held}"

    def test_zero_holdout_holds_nothing_back(self, monkeypatch):
        monkeypatch.setenv(osh.ENV_HOLDOUT, "0")
        assert not any(osh.in_holdout(f"c{i}") for i in range(50))

    def test_a_held_out_conversation_is_not_shaped(self, monkeypatch):
        monkeypatch.setenv(osh.ENV_HOLDOUT, "1")
        d = osh.decide("anthropic", _body(), conversation_key="anything")
        assert not d.shaped and d.reason == "holdout"

    def test_a_malformed_holdout_setting_falls_back(self, monkeypatch):
        monkeypatch.setenv(osh.ENV_HOLDOUT, "not-a-number")
        assert osh.holdout_fraction() == 0.1


class TestNoEffortRouting:
    def test_effort_routing_is_not_implemented(self):
        """Its natural trigger covers 93% of turns, including post-error ones.

        This is a deliberate omission, so it is pinned: reintroducing it should
        be a decision someone makes, not something that reappears by copying a
        competitor's feature list.
        """
        source = (osh.__file__)
        text = open(source).read()
        for token in ("reasoning_effort", "budget_tokens", "output_config"):
            assert token not in text or f'"{token}"' not in text, token

    def test_shaping_never_alters_thinking_parameters(self):
        body = _body()
        body["thinking"] = {"type": "enabled", "budget_tokens": 10000}
        out, d = osh.apply("anthropic", body)
        assert d.shaped
        assert out["thinking"] == {"type": "enabled", "budget_tokens": 10000}


def test_write_tool_detection_is_case_insensitive():
    """Agents disagree on capitalisation, and the guard is safety-critical.

    Claude Code emits `Edit`; jcode emits `edit`. A case-sensitive membership
    test silently stops recognising a code-writing conversation, which is the
    single condition this gate exists to catch. Measured on 11,187 real jcode
    assistant turns, 85 of the turns the shaper would have allowed went on to
    call a write tool.
    """
    import os

    from memor.proxy import output_shaper as osr

    os.environ["MEMOR_OUTPUT_SHAPER"] = "1"
    os.environ["MEMOR_OUTPUT_HOLDOUT"] = "0"
    try:
        for name in ("edit", "Edit", "EDIT", "write", "apply_patch", "MultiEdit"):
            body = {
                "system": "sys",
                "messages": [
                    {"role": "assistant", "content": [
                        {"type": "tool_use", "name": name, "input": {}}]},
                    {"role": "user", "content": "and now explain it"},
                ],
            }
            decision = osr.decide("anthropic", body, conversation_key="k")
            assert not decision.shaped, f"{name!r} was not recognised as a write tool"
            assert decision.reason == "conversation_writes_code"

        # Declared-tools path takes the same lowercase spelling.
        body = {
            "system": "sys",
            "messages": [{"role": "user", "content": "fix the parser"}],
            "tools": [{"name": "edit"}, {"name": "bash"}],
        }
        decision = osr.decide("anthropic", body, conversation_key="k")
        assert not decision.shaped
        assert decision.reason == "change_requested_with_write_tools"
    finally:
        os.environ.pop("MEMOR_OUTPUT_SHAPER", None)
        os.environ.pop("MEMOR_OUTPUT_HOLDOUT", None)


def test_bare_approval_of_a_proposed_change_is_not_shaped():
    """"yes lets do it" carries no change verb but authorises one.

    Found by replaying real transcripts: the residual code-writing turns the
    shaper still allowed were approvals of a plan proposed in an earlier turn.
    The change intent lives in the assistant's proposal, not in the two words
    the user typed, so scanning only the latest user message misses it.
    """
    import os

    from memor.proxy import output_shaper as osr

    os.environ["MEMOR_OUTPUT_SHAPER"] = "1"
    os.environ["MEMOR_OUTPUT_HOLDOUT"] = "0"
    try:
        for reply in ("yes lets do it", "Lets go with A", "yes", "go ahead", "sounds good", "sure"):
            body = {
                "system": "sys",
                "messages": [
                    {"role": "user", "content": "which approach is better?"},
                    {"role": "assistant", "content": [
                        {"type": "text", "text": "Option A: rewrite the parser."}]},
                    {"role": "user", "content": reply},
                ],
                "tools": [{"name": "edit"}, {"name": "bash"}],
            }
            decision = osr.decide("anthropic", body, conversation_key="k")
            assert not decision.shaped, f"{reply!r} was shaped but authorises a change"
    finally:
        os.environ.pop("MEMOR_OUTPUT_SHAPER", None)
        os.environ.pop("MEMOR_OUTPUT_HOLDOUT", None)
