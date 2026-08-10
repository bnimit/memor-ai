"""Turn boundaries and the confound controls behind the recall-effect panel.

Two independent defects made that panel unreadable, and both are the kind that
produce a plausible number rather than an error.

A tool result is delivered as a record typed "user". Counting those as prompts
split every turn at its first tool call, so `tool_call_count` could only ever be
0 or 1 -- 67%/33% across 107,066 real rows -- and the panel's median was 0.0 in
both arms no matter what recall did. Measured on real transcripts, 92% of "user"
records are tool results, and the true median is 4 calls per turn with a maximum
of 87.

Separately, the two arms are not drawn from the same projects: plirin is 42% of
the recalled arm against 16% of the control. Projects differ in how tool-heavy
they are, so the pooled aggregate partly measures which projects get recall.
The naive figure read -33.3% where the within-project figure was -6.1%.
"""
import json
import time

import pytest

from memor.episodes import Episode, project_adjusted_delta, verdict
from memor.turn_metrics import parse_turn_metrics


def _transcript(tmp_path, records):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records))
    return p


def _prompt(text="do the thing", ts="2026-08-10T10:00:00.000Z"):
    return {"type": "user", "timestamp": ts,
            "message": {"content": [{"type": "text", "text": text}]}}


def _assistant_tools(*names):
    return {"type": "assistant", "message": {
        "content": [{"type": "tool_use", "name": n} for n in names]}}


def _tool_result(tool_use_id="x"):
    """What Claude Code writes back after a tool runs: typed 'user'."""
    return {"type": "user", "timestamp": "2026-08-10T10:00:01.000Z",
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}]}}


class TestTurnBoundaries:
    def test_a_turn_accumulates_every_tool_call(self, tmp_path):
        """One prompt, five tools across four assistant messages: one turn, 5."""
        path = _transcript(tmp_path, [
            _prompt(),
            _assistant_tools("Read"), _tool_result(),
            _assistant_tools("Bash"), _tool_result(),
            _assistant_tools("Edit", "Edit"), _tool_result(),
            _assistant_tools("Bash"), _tool_result(),
        ])
        metrics = parse_turn_metrics(path, "s1")
        assert len(metrics) == 1, f"expected one turn, got {len(metrics)}"
        assert metrics[0].tool_call_count == 5

    def test_tool_results_do_not_start_new_turns(self, tmp_path):
        path = _transcript(tmp_path, [
            _prompt(), _assistant_tools("Read"), _tool_result(),
            _assistant_tools("Read"), _tool_result(),
        ])
        assert len(parse_turn_metrics(path, "s1")) == 1

    def test_a_real_prompt_does_start_a_new_turn(self, tmp_path):
        path = _transcript(tmp_path, [
            _prompt("first"), _assistant_tools("Read"), _tool_result(),
            _prompt("second"), _assistant_tools("Bash"), _tool_result(),
        ])
        metrics = parse_turn_metrics(path, "s1")
        assert len(metrics) == 2
        assert [m.tool_call_count for m in metrics] == [1, 1]

    def test_the_last_turn_is_not_dropped(self, tmp_path):
        """Nothing follows it to trigger the flush, so it needs closing."""
        path = _transcript(tmp_path, [_prompt(), _assistant_tools("Read", "Bash")])
        metrics = parse_turn_metrics(path, "s1")
        assert len(metrics) == 1 and metrics[0].tool_call_count == 2

    def test_a_turn_with_no_tools_is_recorded_as_zero(self, tmp_path):
        path = _transcript(tmp_path, [
            _prompt(),
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "here is the answer"}]}},
        ])
        metrics = parse_turn_metrics(path, "s1")
        assert len(metrics) == 1 and metrics[0].tool_call_count == 0

    def test_counts_can_exceed_one(self, tmp_path):
        """The old parser capped at 1; real turns reach 87."""
        records = [_prompt()]
        for _ in range(30):
            records += [_assistant_tools("Bash"), _tool_result()]
        metrics = parse_turn_metrics(_transcript(tmp_path, records), "s1")
        assert metrics[0].tool_call_count == 30


def _ep(project, tool_calls, had_recall):
    return Episode(session_id="s", project=project, conversation_key="k",
                   started_at=time.time(), prompt_chars=100,
                   tool_calls=tool_calls, assistant_steps=1, had_recall=had_recall)


class TestProjectAdjustment:
    def test_project_mix_cannot_manufacture_an_effect(self):
        """Both projects show no within-project difference.

        The recalled arm is mostly the cheap project and the control mostly the
        expensive one, so a pooled comparison shows a large fake saving.
        """
        with_r = [_ep("cheap", 2, True) for _ in range(80)] + \
                 [_ep("costly", 10, True) for _ in range(30)]
        without = [_ep("cheap", 2, False) for _ in range(30)] + \
                  [_ep("costly", 10, False) for _ in range(80)]
        adj = project_adjusted_delta(with_r, without)
        assert adj["scored"]
        assert abs(adj["delta_pct"]) < 1.0, (
            f"project mix leaked into the adjusted figure: {adj['delta_pct']}%")

    def test_a_real_uniform_effect_survives_adjustment(self):
        with_r = [_ep("a", 5, True) for _ in range(40)] + \
                 [_ep("b", 5, True) for _ in range(40)]
        without = [_ep("a", 10, False) for _ in range(40)] + \
                  [_ep("b", 10, False) for _ in range(40)]
        adj = project_adjusted_delta(with_r, without)
        assert adj["scored"] and adj["consistent"]
        assert adj["delta_pct"] == pytest.approx(50.0, abs=1.0)

    def test_projects_disagreeing_on_sign_are_flagged(self):
        with_r = [_ep("a", 2, True) for _ in range(40)] + \
                 [_ep("b", 20, True) for _ in range(40)]
        without = [_ep("a", 10, False) for _ in range(40)] + \
                  [_ep("b", 10, False) for _ in range(40)]
        adj = project_adjusted_delta(with_r, without)
        assert adj["scored"] and not adj["consistent"]

    def test_thin_projects_are_excluded_not_estimated(self):
        with_r = [_ep("big", 5, True) for _ in range(40)] + [_ep("tiny", 1, True)]
        without = [_ep("big", 10, False) for _ in range(40)] + [_ep("tiny", 99, False)]
        adj = project_adjusted_delta(with_r, without)
        assert [c["project"] for c in adj["projects"]] == ["big"]

    def test_no_qualifying_project_is_reported_as_unscored(self):
        adj = project_adjusted_delta([_ep("a", 1, True)], [_ep("a", 2, False)])
        assert adj["scored"] is False


class TestVerdict:
    def _strata(self, *deltas):
        return [{"scored": True, "tool_call_delta_pct": d} for d in deltas]

    def test_disagreeing_projects_yield_no_effect(self):
        overall = {"scored": True, "tool_call_delta_pct": 40.0,
                   "project_adjusted": {"scored": True, "consistent": False,
                                        "delta_pct": 40.0}}
        assert verdict(overall, self._strata(20.0, 25.0)) == "no_effect"

    def test_the_adjusted_figure_is_preferred_over_the_naive_one(self):
        """A large pooled number that vanishes within projects is not an effect."""
        overall = {"scored": True, "tool_call_delta_pct": 33.3,
                   "project_adjusted": {"scored": True, "consistent": True,
                                        "delta_pct": 1.0}}
        assert verdict(overall, self._strata(10.0, 12.0)) == "no_effect"

    def test_a_consistent_effect_is_still_reported(self):
        overall = {"scored": True, "tool_call_delta_pct": 30.0,
                   "project_adjusted": {"scored": True, "consistent": True,
                                        "delta_pct": 28.0}}
        assert verdict(overall, self._strata(25.0, 30.0)) == "saves"
