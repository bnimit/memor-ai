# tests/test_daemon_distiller_select.py
import memor.daemon as d
from memor.llm import llama_cpp as lc


def test_make_llm_prefers_local_when_flag_on(monkeypatch):
    monkeypatch.setenv("MEMOR_LLM_DISTILL", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(lc, "available", lambda: True)
    monkeypatch.setattr(lc.LlamaCppLLM, "__init__", lambda self, **k: None)
    llm = d._make_llm()
    assert isinstance(llm, lc.LlamaCppLLM)


def test_make_llm_flag_off_no_local(monkeypatch):
    monkeypatch.delenv("MEMOR_LLM_DISTILL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert d._make_llm() is None


def test_make_llm_local_unavailable_falls_through(monkeypatch):
    monkeypatch.setenv("MEMOR_LLM_DISTILL", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(lc, "available", lambda: False)
    assert d._make_llm() is None
