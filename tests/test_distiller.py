from memorable.store.sqlite_store import SqliteStore
from memorable.embed.fake import FakeEmbedder
from memorable.distill.distiller import Distiller
from memorable.types import Artifact
import json

class FakeLLM:
    def __init__(self, payload): self.payload = payload
    def complete(self, prompt, *, max_tokens=1024): return json.dumps(self.payload)

def chunk(i, text, t):
    return Artifact(id=i, kind="session_chunk", project="p", source="cc",
                    text=text, token_count=len(text.split()), created_at=t,
                    meta={"session_id":"s1"})

def test_distill_creates_typed_memories_with_provenance(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    chunks = [chunk("s1:0","we decided to use bcrypt for hashing",10)]
    s.add_artifacts(chunks, e.embed([c.text for c in chunks]))
    llm = FakeLLM({"memories":[{"type":"decision","text":"Use bcrypt for password hashing"}]})
    d = Distiller(s, e, llm)
    mem_ids = d.distill_session("s1", chunks, project="p")
    assert len(mem_ids) == 1
    # provenance edge memory --derived_from--> source chunk
    nbrs = [a.id for a in s.neighbors(mem_ids, ["derived_from"], hops=1)]
    assert "s1:0" in nbrs

def test_supersede_on_contradiction(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    c1 = [chunk("s1:0","use bcrypt for hashing",10)]
    s.add_artifacts(c1, e.embed([c.text for c in c1]))
    d = Distiller(s, e, FakeLLM({"memories":[{"type":"decision","text":"Use bcrypt for hashing"}]}))
    old_ids = d.distill_session("s1", c1, project="p")
    c2 = [chunk("s2:0","switch from bcrypt to argon2 for hashing",20)]
    s.add_artifacts(c2, e.embed([c.text for c in c2]))
    d2 = Distiller(s, e, FakeLLM({"memories":[
        {"type":"decision","text":"Use argon2 for hashing","supersedes_text":"Use bcrypt for hashing"}]}))
    new_ids = d2.distill_session("s2", c2, project="p")
    # old memory deactivated, new active
    from memorable.types import Scope
    active = [a.id for a,_ in s.search(e.embed(["hashing"])[0], Scope(project="p"), k=10)]
    assert new_ids[0] in active and old_ids[0] not in active
