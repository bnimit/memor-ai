# tests/test_recall_inject_mode.py
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact
from memor import recall as R


def _setup(tmp_path):
    e = FakeEmbedder(dim=16); p = str(tmp_path/"m.db")
    s = SqliteStore(p, dim=16)
    a = Artifact(id="m1", kind="memory", project="p", source="distill",
                 text="A long rich distilled value about the auth subsystem.",
                 token_count=9, created_at=1000.0,
                 meta={"mem_type": "fact", "fact": "auth uses session cookies"})
    s.add_artifacts([a], e.embed([a.text]))
    s.add_keys("m1", [("fact", "auth uses session cookies")],
               e.embed(["auth uses session cookies"]))
    return e, p


def test_fact_mode_injects_terse_fact(tmp_path, monkeypatch):
    e, p = _setup(tmp_path)
    monkeypatch.setenv("MEMOR_LLM_DISTILL", "1")
    monkeypatch.setenv("MEMOR_INJECT_MODE", "fact")
    res = R.recall("session cookies auth", "p", p, embedder=e, threshold=0.0)
    assert "auth uses session cookies" in res.formatted_context
    assert "long rich distilled value" not in res.formatted_context
    assert res.tokens_injected < 9


def test_fact_mode_fallback_no_fact_key(tmp_path, monkeypatch):
    e = FakeEmbedder(dim=16); p = str(tmp_path/"m.db")
    s = SqliteStore(p, dim=16)
    a = Artifact(id="m2", kind="memory", project="p", source="distill",
                 text="A long rich value with no fact key stored.",
                 token_count=15, created_at=1000.0,
                 meta={"mem_type": "snippet"})
    s.add_artifacts([a], e.embed([a.text]))
    s.add_keys("m2", [("summary", "fact key stored")],
               e.embed(["fact key stored"]))
    monkeypatch.setenv("MEMOR_LLM_DISTILL", "1")
    monkeypatch.setenv("MEMOR_INJECT_MODE", "fact")
    res = R.recall("fact key stored", "p", p, embedder=e, threshold=0.0)
    assert "A long rich value with no fact key stored." in res.formatted_context


def test_value_mode_injects_value(tmp_path, monkeypatch):
    e, p = _setup(tmp_path)
    monkeypatch.setenv("MEMOR_LLM_DISTILL", "1")
    monkeypatch.setenv("MEMOR_INJECT_MODE", "value")
    res = R.recall("session cookies auth", "p", p, embedder=e, threshold=0.0)
    assert "long rich distilled value" in res.formatted_context
