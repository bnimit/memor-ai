# tests/test_redistill.py
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact
import memor.daemon as d

class FakeLLM:
    def complete(self, prompt, *, max_tokens=512, grammar=None):
        return '{"memories":[{"type":"lesson","value":"clean distilled note","fact":"clean fact here"}]}'

def _seed(tmp_path):
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    # raw chunks
    chunks = [Artifact(id=f"c{i}", kind="session_chunk", project="p", source="claude_code",
                       text=f"raw junk chunk {i}", token_count=3, created_at=1000.0+i,
                       meta={"session_id": "s1", "ord": i}) for i in range(3)]
    s.add_artifacts(chunks, e.embed([c.text for c in chunks]))
    # an old junk memory
    old = Artifact(id="old1", kind="memory", project="p", source="distill",
                   text="raw junk chunk 0", token_count=3, created_at=1000.0,
                   meta={"mem_type": "extract", "session_id": "s1"})
    s.add_artifacts([old], e.embed([old.text]))
    return e, s

def test_redistill_deactivates_old_and_creates_clean(tmp_path):
    e, s = _seed(tmp_path)
    stats = d.redistill_project(s, e, FakeLLM(), "p", deactivate_old=True)
    assert stats["deactivated"] == 1
    assert stats["sessions"] == 1
    assert s.db.execute("SELECT active FROM artifacts WHERE id='old1'").fetchone()["active"] == 0
    clean = s.db.execute(
        "SELECT COUNT(*) c FROM artifacts WHERE kind='memory' AND active=1").fetchone()["c"]
    assert clean == stats["memories"] >= 1
