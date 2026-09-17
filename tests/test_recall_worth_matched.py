"""Matched ATT for recall-worth (Approach 1′)."""
from __future__ import annotations

from memor.episodes import (
    Episode,
    MIN_RECALL_CHARS,
    eligible_episodes,
    match_treated_controls,
    matched_att,
    summarize,
    verdict_from_matched,
)


def _ep(*, project="p", recall=False, tools=5, prompt_chars=100, recall_chars=0,
        conversation_key="c", started_at=0.0):
    return Episode(
        project=project,
        conversation_key=conversation_key,
        started_at=started_at,
        had_recall=recall,
        recall_chars=recall_chars if recall else 0,
        tool_calls=tools,
        assistant_steps=1,
        prompt_chars=prompt_chars,
    )


def test_weak_inject_excluded_from_both_arms():
    weak = _ep(recall=True, recall_chars=MIN_RECALL_CHARS - 1, tools=2)
    strong = _ep(recall=True, recall_chars=MIN_RECALL_CHARS, tools=2)
    control = _ep(recall=False, tools=8)
    treated, controls, excluded = eligible_episodes([weak, strong, control])
    assert excluded == 1
    assert len(treated) == 1 and treated[0] is strong
    assert len(controls) == 1


def test_match_same_project_and_band():
    treated = [_ep(recall=True, recall_chars=200, tools=2, prompt_chars=80,
                   conversation_key=f"t{i}", started_at=float(i)) for i in range(5)]
    controls = (
        [_ep(project="other", recall=False, tools=9, prompt_chars=80,
             conversation_key=f"o{i}", started_at=float(i)) for i in range(5)]
        + [_ep(recall=False, tools=9, prompt_chars=80,
               conversation_key=f"c{i}", started_at=float(i)) for i in range(5)]
    )
    pairs, stats = match_treated_controls(treated, controls)
    assert len(pairs) == 5
    assert all(t.project == c.project for t, c in pairs)
    assert stats["match_rate"] == 1.0


def test_ratio_caliper_rejects_unlike_prompts():
    treated = [_ep(recall=True, recall_chars=200, tools=2, prompt_chars=100)]
    controls = [_ep(recall=False, tools=9, prompt_chars=500)]  # ratio 5 > 3
    pairs, stats = match_treated_controls(treated, controls)
    assert pairs == []
    assert stats["unmatched_treated"] == 1


def test_without_replacement_prefers_unused_controls():
    treated = [
        _ep(recall=True, recall_chars=200, tools=1, prompt_chars=100,
            conversation_key="t0", started_at=0),
        _ep(recall=True, recall_chars=200, tools=1, prompt_chars=105,
            conversation_key="t1", started_at=1),
    ]
    controls = [
        _ep(recall=False, tools=8, prompt_chars=100, conversation_key="c0", started_at=0),
    ]
    pairs, stats = match_treated_controls(treated, controls)
    # Second treated may reuse the only control; first pass is without replacement.
    assert len(pairs) == 2
    assert stats["reuse_rate"] == 0.5
    assert stats["unmatched_treated"] == 0


def test_unmatched_when_no_control_in_band():
    treated = [_ep(recall=True, recall_chars=200, tools=1, prompt_chars=100)]
    controls = [_ep(recall=False, tools=8, prompt_chars=500)]  # different band + ratio
    pairs, stats = match_treated_controls(treated, controls)
    assert pairs == []
    assert stats["unmatched_treated"] == 1
    assert stats["match_rate"] == 0.0


def test_matched_att_positive_means_fewer_tools():
    treated = [
        _ep(recall=True, recall_chars=200, tools=2, prompt_chars=100,
            conversation_key=f"t{i}", started_at=float(i))
        for i in range(10)
    ]
    controls = [
        _ep(recall=False, tools=8, prompt_chars=100,
            conversation_key=f"c{i}", started_at=float(i))
        for i in range(10)
    ]
    pairs, _ = match_treated_controls(treated, controls)
    att = matched_att(pairs)
    assert att["n_pairs"] == 10
    assert att["tool_call_delta_pct"] > 0  # fewer tools with recall


def test_verdict_insufficient_on_low_match_rate():
    att = {"tool_call_delta_pct": 40.0, "mde_pct": 5.0, "n_pairs": 80}
    assert verdict_from_matched(att, match_rate=0.4) == "insufficient_data"


def test_verdict_saves_when_att_clears_floor():
    att = {"tool_call_delta_pct": 25.0, "mde_pct": 8.0, "n_pairs": 80}
    assert verdict_from_matched(att, match_rate=0.9) == "saves"


def test_verdict_costs_when_att_negative():
    att = {"tool_call_delta_pct": -25.0, "mde_pct": 8.0, "n_pairs": 80}
    assert verdict_from_matched(att, match_rate=0.9) == "costs"


def test_verdict_no_effect_below_threshold():
    att = {"tool_call_delta_pct": 5.0, "mde_pct": 2.0, "n_pairs": 80}
    assert verdict_from_matched(att, match_rate=0.9) == "no_effect"


def test_summarize_headline_is_matched_att_not_stratum_flip():
    """Long-prompt band disagrees; matched mid-band pairs still decide."""
    # Mid band (60-150): recall helps. Long band (400+): recall "hurts" but
    # fewer pairs — matched ATT across all should still be driven by volume.
    with_mid = [
        _ep(recall=True, recall_chars=200, tools=2, prompt_chars=100,
            conversation_key=f"tm{i}", started_at=float(i))
        for i in range(40)
    ]
    without_mid = [
        _ep(recall=False, tools=9, prompt_chars=100,
            conversation_key=f"cm{i}", started_at=float(i))
        for i in range(40)
    ]
    with_long = [
        _ep(recall=True, recall_chars=200, tools=9, prompt_chars=500,
            conversation_key=f"tl{i}", started_at=float(i))
        for i in range(10)
    ]
    without_long = [
        _ep(recall=False, tools=2, prompt_chars=500,
            conversation_key=f"cl{i}", started_at=float(i))
        for i in range(10)
    ]
    s = summarize(with_mid + without_mid + with_long + without_long)
    assert s["claim_scope"] == "claude_code_episodes"
    assert s["overall"]["matched"]["n_pairs"] >= 50
    assert s["overall"]["matched"]["match_rate"] >= 0.6
    # Mid volume dominates → saves; 400-+ must not veto via sign flip.
    assert s["overall"]["verdict"] == "saves"
    assert s["overall"]["matched_att_pct"] == s["overall"]["matched"]["tool_call_delta_pct"]
