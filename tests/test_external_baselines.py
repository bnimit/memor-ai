from memorable.eval.baselines.base import ExternalBaseline

class StubBaseline(ExternalBaseline):
    name = "stub"
    def available(self): return True
    def index(self, artifacts): self._texts = {a.id: a.text for a in artifacts}
    def retrieve(self, query, project, k): return list(self._texts.keys())[:k]

def test_external_baseline_contract():
    from memorable.types import Artifact
    b = StubBaseline()
    assert b.available()
    b.index([Artifact(id="x",kind="note",project="p",source="t",text="hi",
                      token_count=1,created_at=0,meta={})])
    assert b.retrieve("q","p",5) == ["x"]
