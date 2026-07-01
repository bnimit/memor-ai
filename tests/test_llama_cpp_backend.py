# tests/test_llama_cpp_backend.py
import builtins, pytest
from memor.llm import llama_cpp as mod

def test_available_reflects_import(monkeypatch):
    # Force the import to fail and confirm available() returns False.
    real_import = builtins.__import__
    def fake_import(name, *a, **k):
        if name == "llama_cpp":
            raise ImportError("no llama_cpp")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert mod.available() is False

def test_unavailable_raises_on_construct(monkeypatch):
    monkeypatch.setattr(mod, "available", lambda: False)
    with pytest.raises(mod.LlamaCppUnavailable):
        mod.LlamaCppLLM()

def test_complete_forwards_grammar(monkeypatch):
    # Fake Llama that records the grammar and returns canned JSON.
    calls = {}
    class FakeLlama:
        def __init__(self, **kw): calls["init"] = kw
        def create_completion(self, prompt, **kw):
            calls["kw"] = kw
            return {"choices": [{"text": '{"memories":[]}'}]}
    class FakeGrammar:
        @staticmethod
        def from_string(s): calls["grammar"] = s; return "GRAMMAR_OBJ"
    monkeypatch.setattr(mod, "available", lambda: True)
    monkeypatch.setattr(mod, "_load_llama", lambda self: FakeLlama())
    monkeypatch.setattr(mod, "_LlamaGrammar", FakeGrammar)
    llm = mod.LlamaCppLLM()
    out = llm.complete("hi", grammar="root ::= \"x\"")
    assert out == '{"memories":[]}'
    assert calls["grammar"] == 'root ::= "x"'
    assert calls["kw"]["temperature"] == 0.0
    assert calls["kw"]["grammar"] == "GRAMMAR_OBJ"
