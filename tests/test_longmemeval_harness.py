"""Tests for the LongMemEval harness.

The dataset is 277 MB and not vendored, so the scoring logic is tested against a
synthetic haystack built in the same shape. That is deliberate: what can silently
go wrong here is the scoring, not the JSON parsing. A harness that reports a
number nobody can falsify is exactly the failure this repo already had.
"""
import json
import pathlib

import pytest

from memor.eval import longmemeval as lme


def _instance(qid: str, qtype: str, n_sessions: int = 3, answer_at: int = 1) -> dict:
    sessions, sids = [], []
    for s in range(n_sessions):
        sids.append(f"{qid}_s{s}")
        turns = [{"role": "user", "content": f"filler chatter about topic {s}"},
                 {"role": "assistant", "content": f"reply about topic {s}"}]
        if s == answer_at:
            turns.append({"role": "user",
                          "content": "my car GPS stopped working after the service",
                          "has_answer": True})
        sessions.append(turns)
    return {
        "question_id": qid, "question_type": qtype,
        "question": "what went wrong with the car GPS",
        "answer": "GPS system not functioning",
        "question_date": "2023/05/20 (Sat) 10:00",
        "haystack_dates": ["2023/05/01"] * n_sessions,
        "haystack_session_ids": sids,
        "haystack_sessions": sessions,
        "answer_session_ids": [sids[answer_at]],
    }


@pytest.fixture
def dataset(tmp_path: pathlib.Path) -> pathlib.Path:
    types = ["single-session-user", "multi-session", "temporal-reasoning",
             "knowledge-update", "single-session-assistant",
             "single-session-preference"]
    data = [_instance(f"q{i}_{t}", t) for t in types for i in range(3)]
    data.append(_instance("q_skipme_abs", "multi-session"))
    p = tmp_path / "lme.json"
    p.write_text(json.dumps(data))
    return p


def test_abstention_instances_are_excluded(dataset):
    """The benchmark instructs skipping these: they have no ground-truth location."""
    cases = lme.load_cases(dataset, n=100)
    assert cases, "sanity: some cases loaded"
    assert not any(c["question_id"].endswith("_abs") for c in cases)


def test_sampling_is_stratified_not_a_head_slice(dataset):
    """A head slice would score one ability and report it as an overall number.

    The real file is grouped by question type, so `[:n]` is not a neutral sample.
    """
    cases = lme.load_cases(dataset, n=12)
    assert len(cases) == 12
    kinds = {c["question_type"] for c in cases}
    assert len(kinds) == 6, f"expected all 6 abilities represented, got {kinds}"


def test_sampling_is_deterministic(dataset):
    """A benchmark whose sample moves between runs cannot show a regression."""
    a = [c["question_id"] for c in lme.load_cases(dataset, n=12)]
    b = [c["question_id"] for c in lme.load_cases(dataset, n=12)]
    assert a == b


def test_missing_data_names_the_download(tmp_path):
    with pytest.raises(FileNotFoundError) as e:
        lme.run(n=2, data_path=tmp_path / "absent.json")
    assert "longmemeval" in str(e.value).lower()
    assert "curl" in str(e.value), "the error should tell the user how to fix it"


def test_all_gold_is_never_more_lenient_than_any_hit(dataset):
    """all-gold is a subset condition of any-hit; if it ever exceeds it, the
    metric is inverted and every published figure from it would be wrong."""
    r = lme.run(n=6, k=8, data_path=dataset)
    assert r.all_gold <= r.any_hit
    for t, v in r.by_type.items():
        assert v["all_gold"] <= v["any_hit"], t


def test_retrieval_finds_the_planted_evidence(dataset):
    """End-to-end: the evidence turn is lexically obvious, so a working
    retriever must find it. A near-zero score here means the harness is broken
    rather than the retriever being bad."""
    r = lme.run(n=6, k=8, data_path=dataset)
    assert r.n == 6
    assert r.any_hit >= 80.0, f"harness or retriever broken: {r.any_hit}%"


def test_format_result_reports_both_metrics(dataset):
    out = lme.format_result(lme.run(n=6, k=8, data_path=dataset))
    assert "any-hit" in out and "all-gold" in out
    assert "n=6" in out, "the population must be printed with the figure"
