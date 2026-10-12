"""Counterfactual dispute-stratum helpers."""
from __future__ import annotations

from memor.eval.counterfactual import (
    hits_contain_dispute_pair,
    summarize_by_stratum,
)


def test_hits_contain_dispute_pair(tmp_path):
    from memor.embed.fake import FakeEmbedder
    from memor.store.sqlite_store import SqliteStore
    from memor.types import Artifact

    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    e = FakeEmbedder(dim=16)
    for mid, text, t in (("o", "old", 1.0), ("m", "new", 2.0), ("x", "other", 3.0)):
        s.add_artifacts([
            Artifact(id=mid, kind="memory", project="p", source="t", text=text,
                     token_count=1, created_at=t, meta={"mem_type": "decision"})
        ], e.embed([text]))
    s.add_dispute("o", "m")
    assert hits_contain_dispute_pair(s, ["o", "m"])
    assert not hits_contain_dispute_pair(s, ["o", "x"])
    assert not hits_contain_dispute_pair(s, ["o"])


def test_summarize_by_stratum():
    rows = [
        {"outcome": "win", "dispute_present": True},
        {"outcome": "loss", "dispute_present": True},
        {"outcome": "tie", "dispute_present": False},
        {"outcome": "tie", "dispute_present": False},
    ]
    out = summarize_by_stratum(rows)
    assert out["dispute_present"]["n_cases"] == 2
    assert out["dispute_present"]["win_count"] == 1
    assert out["dispute_present"]["loss_count"] == 1
    assert out["dispute_present"]["do_no_harm_pct"] == 50.0
    assert out["no_dispute"]["n_cases"] == 2
    assert out["no_dispute"]["do_no_harm_pct"] == 100.0
