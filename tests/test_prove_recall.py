"""Prove-recall decision tree and thrift gate."""
from __future__ import annotations

from memor.prove_recall import (
    decide_prove_recall,
    gate_g0_meter,
    offline_thrift_ok,
    _kinds_from_formatted,
)


def test_g0_requires_pairs_and_match_rate():
    ok, _ = gate_g0_meter({
        "verdict": "no_effect",
        "matched": {"n_pairs": 100, "match_rate": 0.9},
    })
    assert ok
    bad, _ = gate_g0_meter({
        "verdict": "no_effect",
        "matched": {"n_pairs": 10, "match_rate": 0.9},
    })
    assert not bad


def test_g1_thrift_and_memory_share():
    ok, reason = offline_thrift_ok(
        default_mean_tokens=1000,
        strict_mean_tokens=700,
        default_memory_share=0.2,
        strict_memory_share=0.5,
        n_compared=20,
    )
    assert ok
    assert "70%" in reason or "0.7" in reason or "tokens" in reason

    fail, _ = offline_thrift_ok(
        default_mean_tokens=1000,
        strict_mean_tokens=950,
        default_memory_share=0.2,
        strict_memory_share=0.5,
        n_compared=20,
    )
    assert not fail


def test_decision_tree_needs_forward_when_g0_g1():
    d = decide_prove_recall(g0=True, g1=True, g2_due=False)
    assert d["decision"] == "needs_forward_window"


def test_decision_tree_fail_policy():
    assert decide_prove_recall(g0=True, g1=False)["decision"] == "fail_policy"


def test_decision_tree_pass_roi():
    d = decide_prove_recall(g0=True, g1=True, g2_due=True, g2_verdict="saves")
    assert d["decision"] == "pass_roi"


def test_kinds_from_formatted():
    text = "## Recalled\n### 1. [decision] we use postgres\n### 2. [session_chunk] foo\n"
    assert _kinds_from_formatted(text) == ["decision", "session_chunk"]
