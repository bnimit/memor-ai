"""A noisy stratum must not veto a well-powered aggregate.

The verdict called `no_effect` whenever prompt-size bands disagreed in sign.
Sound against composition, but it counted every scored band equally, and a band
of 250 episodes cannot resolve a difference smaller than about a third -- tool
calls per episode have a standard deviation larger than their mean.

On the real store three of four bands sat below their own detectable floor, so
their signs were coin flips. The aggregate meanwhile showed +22.5% against a
13.5% floor: detectable, and vetoed by noise. The panel could never move, and
"not enough data yet" was the wrong explanation -- more data would not have
changed the rule.
"""
import time

import pytest

from memor.episodes import Episode, stratified_deltas, verdict


def _ep(prompt_chars, tool_calls, had_recall, project="p"):
    return Episode(session_id="s", project=project, conversation_key="k",
                   started_at=time.time(), prompt_chars=prompt_chars,
                   tool_calls=tool_calls, assistant_steps=1, had_recall=had_recall)


class TestStratumSignificance:
    def test_a_tight_cell_is_significant(self):
        """Low variance and a clear gap: this band has earned a sign."""
        w = [_ep(30, 2, True) for _ in range(60)]
        n = [_ep(30, 6, False) for _ in range(60)]
        cell = stratified_deltas(w, n)[0]
        assert cell["scored"] and cell["significant"]
        assert cell["mde_pct"] is not None

    def test_a_noisy_cell_is_not_significant(self):
        """Same means, huge spread: the sign here is a coin flip."""
        w = [_ep(30, v, True) for v in ([0] * 30 + [40] * 30)]
        n = [_ep(30, v, False) for v in ([0] * 29 + [40] * 31)]
        cell = stratified_deltas(w, n)[0]
        assert cell["scored"]
        assert not cell["significant"]

    def test_an_unscored_cell_reports_no_floor(self):
        cell = stratified_deltas([_ep(30, 1, True)], [_ep(30, 2, False)])[0]
        assert not cell["scored"] and cell["mde_pct"] is None


class TestVerdict:
    def _cells(self, *specs):
        return [{"scored": True, "tool_call_delta_pct": d, "significant": s}
                for d, s in specs]

    def test_noise_cells_do_not_veto_a_real_aggregate(self):
        """The bug. Three noisy bands disagreed; one real band agreed."""
        overall = {"scored": True, "tool_call_delta_pct": 22.5,
                   "project_adjusted": {"scored": True, "consistent": True,
                                        "delta_pct": 20.3}}
        strata = self._cells((17.0, False), (-15.9, False),
                             (-17.8, False), (60.8, True))
        assert verdict(overall, strata) == "saves"

    def test_two_significant_cells_disagreeing_still_veto(self):
        """Genuine composition must still be caught."""
        overall = {"scored": True, "tool_call_delta_pct": 20.0,
                   "project_adjusted": {"scored": True, "consistent": True,
                                        "delta_pct": 20.0}}
        strata = self._cells((40.0, True), (-35.0, True))
        assert verdict(overall, strata) == "no_effect"

    def test_one_significant_cell_is_not_a_disagreement(self):
        overall = {"scored": True, "tool_call_delta_pct": 25.0,
                   "project_adjusted": {"scored": True, "consistent": True,
                                        "delta_pct": 25.0}}
        assert verdict(overall, self._cells((30.0, True), (-5.0, False))) == "saves"

    def test_a_negative_effect_is_still_reported_as_costs(self):
        overall = {"scored": True, "tool_call_delta_pct": -30.0,
                   "project_adjusted": {"scored": True, "consistent": True,
                                        "delta_pct": -28.0}}
        assert verdict(overall, self._cells((-30.0, True), (-25.0, True))) == "costs"

    def test_a_small_effect_is_still_no_effect(self):
        overall = {"scored": True, "tool_call_delta_pct": 1.0,
                   "project_adjusted": {"scored": True, "consistent": True,
                                        "delta_pct": 0.5}}
        assert verdict(overall, self._cells((2.0, True), (1.0, True))) == "no_effect"

    def test_project_disagreement_still_vetoes(self):
        """The other confound control must keep working."""
        overall = {"scored": True, "tool_call_delta_pct": 40.0,
                   "project_adjusted": {"scored": True, "consistent": False,
                                        "delta_pct": 40.0}}
        assert verdict(overall, self._cells((30.0, True), (35.0, True))) == "no_effect"
