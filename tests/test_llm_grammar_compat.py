"""Test that cloud LLM backends accept and ignore grammar kwarg."""


def test_openai_compat_accepts_and_ignores_grammar(monkeypatch):
    """OpenAICompatLLM should accept grammar kwarg and ignore it."""
    import memor.llm.openai_compat as mod

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResp())
    llm = mod.OpenAICompatLLM(base_url="http://x", api_key="k", model="m")
    result = llm.complete("hi", grammar="root ::= \"x\"")
    assert result == "ok"  # no TypeError


def test_anthropic_accepts_and_ignores_grammar():
    """AnthropicLLM should accept grammar kwarg and ignore it."""
    from memor.llm.anthropic import AnthropicLLM

    # Skip __init__ to avoid needing the anthropic package
    llm = object.__new__(AnthropicLLM)
    llm.model = "m"
    llm.max_retries = 0

    # Create fake response structure matching anthropic SDK
    class TextBlock:
        type = "text"
        text = "ok"

    class Message:
        content = [TextBlock()]

    class MessagesAPI:
        @staticmethod
        def create(**k):
            return Message()

    class FakeClient:
        messages = MessagesAPI()

    llm.client = FakeClient()
    result = llm.complete("hi", grammar="g")
    assert result == "ok"  # no TypeError, grammar ignored
