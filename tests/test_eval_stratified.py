import time
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact
from memor.eval.counterfactual import (
    partition_cases, summarize_stratified, sample_dispute_precision,
    CounterfactualCase, CounterfactualVerdict, Outcome)


class FakeLLM:
    def __init__(self, reply): self.reply = reply
    def complete(self, prompt): return self.reply


def test_summarize_stratified_keys():
    present = [CounterfactualVerdict(Outcome.WIN, "", 1.0)]
    absent = [CounterfactualVerdict(Outcome.TIE, "", 1.0)]
    out = summarize_stratified(present, absent)
    assert out["dispute_present"]["n_cases"] == 1
    assert out["no_dispute"]["n_cases"] == 1
    assert "do_no_harm_pct" in out["dispute_present"]


def test_dispute_precision_judges_pairs(tmp_path):
    now = time.time()
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    arts = [Artifact(id=i, kind="memory", project="p", source="s", text=i,
                     token_count=1, created_at=now, meta={"mem_type":"decision"})
            for i in ("old","new")]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    s.add_dispute("old","new", now)
    out = sample_dispute_precision(s, FakeLLM('{"contradiction": true}'), n=10)
    assert out["sampled"] == 1 and out["precision"] == 1.0
