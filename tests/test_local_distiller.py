import json
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.distill.distiller import LocalDistiller
from memor.types import Artifact

class FakeLLM:
    def __init__(self, payload): self.payload = payload; self.calls = 0
    def complete(self, prompt, *, max_tokens=512, grammar=None):
        self.calls += 1
        return self.payload

def _chunks(project="p", sid="s1"):
    return [Artifact(id=f"c{i}", kind="session_chunk", project=project, source="t",
                     text=f"line about auth and tokens {i}", token_count=6,
                     created_at=1000.0 + i, meta={"session_id": sid, "ord": i})
            for i in range(3)]

def test_distill_stores_value_artifact_and_fact_key(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    llm = FakeLLM('{"memories":[{"type":"decision","value":"We switched auth to session cookies.","fact":"auth uses session cookies"}]}')
    d = LocalDistiller(s, e, llm, gen_questions=False)
    ids = d.distill_session("s1", _chunks(), "p")
    assert len(ids) == 1
    row = s.db.execute("SELECT * FROM artifacts WHERE id=?", (ids[0],)).fetchone()
    assert row["kind"] == "memory"
    meta = json.loads(row["meta"])
    assert meta["mem_type"] == "decision" and meta["fact"] == "auth uses session cookies"
    # exactly one 'fact' key, no questions
    assert s.count_keys() == 1

def test_distill_generates_question_keys_when_enabled(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    llm = FakeLLM('{"memories":[{"type":"fact","value":"v","fact":"f","questions":["q1","q2"]}]}')
    d = LocalDistiller(s, e, llm, gen_questions=True)
    d.distill_session("s1", _chunks(), "p")
    types = [r["key_type"] for r in s.db.execute("SELECT key_type FROM key_vectors").fetchall()]
    assert types.count("fact") == 1 and types.count("question") == 2

def test_distill_dedups_on_fact(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    llm = FakeLLM('{"memories":[{"type":"fact","value":"v","fact":"identical fact text"}]}')
    d = LocalDistiller(s, e, llm, gen_questions=False)
    d.distill_session("s1", _chunks(sid="s1"), "p")
    second = d.distill_session("s2", _chunks(sid="s2"), "p")  # same fact -> dedup
    assert second == []
    assert s.db.execute("SELECT COUNT(*) c FROM artifacts WHERE kind='memory'").fetchone()["c"] == 1

def test_distill_never_stores_raw_chunk_text(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    llm = FakeLLM('{"memories":[{"type":"lesson","value":"distilled note","fact":"distilled fact"}]}')
    d = LocalDistiller(s, e, llm, gen_questions=False)
    d.distill_session("s1", _chunks(), "p")
    texts = [r["text"] for r in s.db.execute("SELECT text FROM artifacts WHERE kind='memory'").fetchall()]
    assert all("line about auth and tokens" not in t for t in texts)
