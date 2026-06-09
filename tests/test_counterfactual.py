"""Tests for the counterfactual eval harness — win/tie/loss vs no-memory baseline."""
import json
import time

from memor.types import Artifact
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.eval.counterfactual import (
    build_cases_from_store, CounterfactualCase, CounterfactualVerdict,
    Outcome, summarize_verdicts, parse_verdict_json,
)


def _seed_store(tmp_path, *, n_sessions=3, turns_per_session=5):
    """Create a store with sessions that have recall logs and artifacts."""
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    store = SqliteStore(db_path, dim=16)

    now = time.time()
    for s_idx in range(n_sessions):
        sid = f"session-{s_idx}"
        session_start = now - (n_sessions - s_idx) * 86400
        artifacts = []
        vectors = []
        for t_idx in range(turns_per_session):
            aid = f"a-{s_idx}-{t_idx}"
            text = f"Turn {t_idx} of session {s_idx}: discussion about auth middleware and token refresh"
            vec = e.embed([text])[0]
            artifacts.append(Artifact(
                id=aid, kind="session_chunk", project="testproj",
                source="test", text=text, token_count=20,
                created_at=session_start + t_idx,
                meta={"session_id": sid, "ord": t_idx}))
            vectors.append(vec)
        store.add_artifacts(artifacts, vectors)

    store.db.commit()
    return store


def test_build_cases_from_store(tmp_path):
    store = _seed_store(tmp_path, n_sessions=3, turns_per_session=5)
    cases = build_cases_from_store(store, project="testproj",
                                   holdout_turns=2, min_session_turns=4)
    assert len(cases) >= 1
    for c in cases:
        assert c.query
        assert c.holdout_texts
        assert c.scope_project == "testproj"


def test_build_cases_needs_prior_sessions(tmp_path):
    store = _seed_store(tmp_path, n_sessions=1, turns_per_session=5)
    cases = build_cases_from_store(store, project="testproj")
    assert len(cases) == 0


def test_parse_verdict_json_win():
    raw = '{"outcome": "win", "reasoning": "Context saved rework", "confidence": 0.9}'
    v = parse_verdict_json(raw)
    assert v.outcome == Outcome.WIN
    assert v.confidence == 0.9


def test_parse_verdict_json_loss():
    raw = '{"outcome": "loss", "reasoning": "Stale info misled agent", "confidence": 0.8}'
    v = parse_verdict_json(raw)
    assert v.outcome == Outcome.LOSS


def test_parse_verdict_json_tie():
    raw = '{"outcome": "tie", "reasoning": "Irrelevant context", "confidence": 0.7}'
    v = parse_verdict_json(raw)
    assert v.outcome == Outcome.TIE


def test_parse_verdict_embedded_json():
    raw = 'Here is my assessment:\n```json\n{"outcome": "win", "reasoning": "Helped", "confidence": 0.85}\n```'
    v = parse_verdict_json(raw)
    assert v.outcome == Outcome.WIN


def test_summarize_verdicts():
    verdicts = [
        CounterfactualVerdict(outcome=Outcome.WIN, reasoning="a", confidence=0.9),
        CounterfactualVerdict(outcome=Outcome.WIN, reasoning="b", confidence=0.8),
        CounterfactualVerdict(outcome=Outcome.TIE, reasoning="c", confidence=0.7),
        CounterfactualVerdict(outcome=Outcome.LOSS, reasoning="d", confidence=0.6),
    ]
    s = summarize_verdicts(verdicts)
    assert s["win_count"] == 2
    assert s["tie_count"] == 1
    assert s["loss_count"] == 1
    assert s["n_cases"] == 4
    assert s["win_pct"] == 50.0
    assert s["loss_pct"] == 25.0
    assert s["do_no_harm_pct"] == 75.0


def test_summarize_empty():
    s = summarize_verdicts([])
    assert s["n_cases"] == 0
    assert s["do_no_harm_pct"] == 100.0
