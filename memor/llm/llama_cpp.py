from __future__ import annotations


class LlamaCppUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import llama_cpp  # noqa: F401
        return True
    except Exception:
        return False


# Indirection points so tests can substitute fakes without a real model.
_LlamaGrammar = None


def _load_llama(self):
    from llama_cpp import Llama
    return Llama.from_pretrained(
        repo_id=self.repo_id, filename=self.filename,
        n_ctx=self.n_ctx, verbose=False)


class LlamaCppLLM:
    """Local, in-process, offline GGUF distiller. Greedy (temp=0). Ingest-only.

    Never used at recall time. Downloads the GGUF on first use via
    llama-cpp-python's HF integration; caller is responsible for consent
    (see daemon gating)."""

    def __init__(self, repo_id: str = "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
                 filename: str = "*q4_k_m.gguf", n_ctx: int = 4096):
        if not available():
            raise LlamaCppUnavailable(
                "llama-cpp-python is not installed; distillation falls back to extractive")
        global _LlamaGrammar
        if _LlamaGrammar is None:
            from llama_cpp import LlamaGrammar as _LG
            _LlamaGrammar = _LG
        self.repo_id, self.filename, self.n_ctx = repo_id, filename, n_ctx
        self._llama = None

    def _ensure(self):
        if self._llama is None:
            self._llama = _load_llama(self)
        return self._llama

    def complete(self, prompt: str, *, max_tokens: int = 512,
                 grammar: str | None = None) -> str:
        llama = self._ensure()
        kw = {"max_tokens": max_tokens, "temperature": 0.0}
        if grammar is not None:
            kw["grammar"] = _LlamaGrammar.from_string(grammar)
        out = llama.create_completion(prompt, **kw)
        return out["choices"][0]["text"]
