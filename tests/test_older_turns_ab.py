"""The A/B must be able to say "this costs money", or it is not a safeguard.

Compressing older turns is worth ~10% gross on the mass it exposes. But it
rewrites the cached prefix, and Anthropic bills a cache write at 1.25x against a
cache read at 0.1x. With 165,974,863 cache-read tokens in the real ledger, the
overhead can exceed the tokens removed.

The test that matters most is therefore the negative one: a treatment arm with
better gross savings and worse billed cost must be reported as `costs`.
"""
import pytest

from memor.eval.older_turns_ab import (
    MIN_PER_ARM,
    compare,
    experiment_fraction,
    format_report,
    in_treatment,
)


def _row(arm, *, inp=1000, cache_read=0, cache_creation=0, before=0, after=0):
    return {
        "arm": arm,
        "upstream_input_tokens": inp,
        "upstream_cache_read_tokens": cache_read,
        "upstream_cache_creation_tokens": cache_creation,
        "tokens_before": before,
        "tokens_after": after,
    }


def _rows(arm, n, **kw):
    return [_row(arm, **kw) for _ in range(n)]


class TestNetNegativeDetection:
    def test_cache_re_formation_outweighing_the_saving_is_reported_as_costs(self):
        """The whole point of the experiment.

        Treatment removes 2,000 tokens per request but converts 100,000 cached
        reads into cache writes. At 1.25x against 0.1x that is 115,000 extra
        billed units to save 2,000.
        """
        control = _rows("control", 100, inp=1000, cache_read=100_000,
                        before=10_000, after=10_000)
        treatment = _rows("treatment", 100, inp=1000, cache_read=0,
                          cache_creation=100_000, before=10_000, after=8_000)
        result = compare(control + treatment)
        assert result["scored"]
        assert result["gross_delta_pct"] > 0, "gross savings look positive"
        assert result["net_delta_pct"] < 0, "but billing is worse"
        assert result["verdict"] == "costs"

    def test_a_genuine_saving_is_reported_as_saves(self):
        control = _rows("control", 100, inp=10_000, cache_read=50_000)
        treatment = _rows("treatment", 100, inp=7_000, cache_read=50_000,
                          before=10_000, after=7_000)
        result = compare(control + treatment)
        assert result["verdict"] == "saves"
        assert result["net_delta_pct"] > 0

    def test_a_small_difference_is_not_called_an_effect(self):
        control = _rows("control", 100, inp=10_000, cache_read=1_000)
        treatment = _rows("treatment", 100, inp=9_960, cache_read=1_000)
        assert compare(control + treatment)["verdict"] == "no_effect"

    def test_cache_writes_are_priced_above_cache_reads(self):
        """A pure read-to-write conversion must register as more expensive."""
        control = _rows("control", 100, inp=0, cache_read=100_000)
        treatment = _rows("treatment", 100, inp=0, cache_creation=100_000)
        result = compare(control + treatment)
        assert result["net_delta_pct"] < 0
        assert result["treatment"]["billed_per_request"] > \
               result["control"]["billed_per_request"]


class TestGuards:
    def test_thin_arms_are_not_scored(self):
        rows = _rows("control", 5) + _rows("treatment", 5)
        result = compare(rows)
        assert not result["scored"]
        assert result["verdict"] == "insufficient_data"

    def test_one_full_arm_is_still_not_enough(self):
        rows = _rows("control", MIN_PER_ARM + 10) + _rows("treatment", 3)
        assert compare(rows)["verdict"] == "insufficient_data"

    def test_rows_without_usage_are_dropped(self):
        """A row whose upstream numbers never arrived cannot say what it cost."""
        rows = _rows("control", MIN_PER_ARM) + _rows("treatment", MIN_PER_ARM)
        blank = dict(rows[0])
        blank["upstream_input_tokens"] = None
        result = compare(rows + [blank])
        assert result["control"]["requests"] == MIN_PER_ARM

    def test_unknown_arms_are_ignored(self):
        rows = _rows("control", MIN_PER_ARM) + _rows("treatment", MIN_PER_ARM)
        rows.append(_row("something-else"))
        result = compare(rows)
        assert result["control"]["requests"] + result["treatment"]["requests"] \
            == 2 * MIN_PER_ARM

    def test_empty_input_does_not_raise(self):
        assert compare([])["verdict"] == "insufficient_data"


class TestAssignment:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("MEMOR_OLDER_TURNS_EXPERIMENT", raising=False)
        assert experiment_fraction() == 0.0
        assert not in_treatment("any-conversation")

    def test_assignment_is_stable_for_a_conversation(self):
        """Flipping arms mid-conversation would rewrite the prefix on the
        switch, charging the experiment for an effect it does not have."""
        first = in_treatment("conv-1", fraction=0.5)
        for _ in range(50):
            assert in_treatment("conv-1", fraction=0.5) is first

    def test_the_split_is_roughly_the_requested_fraction(self):
        treated = sum(in_treatment(f"conv-{i}", fraction=0.5) for i in range(600))
        assert 250 < treated < 350, treated

    def test_a_malformed_fraction_disables_rather_than_enables(self, monkeypatch):
        monkeypatch.setenv("MEMOR_OLDER_TURNS_EXPERIMENT", "yes-please")
        assert experiment_fraction() == 0.0


class TestReport:
    def test_a_costing_result_says_why(self):
        control = _rows("control", 100, inp=0, cache_read=100_000)
        treatment = _rows("treatment", 100, inp=0, cache_creation=100_000,
                          before=1000, after=900)
        text = format_report(compare(control + treatment))
        assert "costs" in text
        assert "cache re-formation" in text

    def test_both_gross_and_net_are_reported(self):
        """Reporting only gross is how a net loss gets shipped as a win."""
        control = _rows("control", 100, inp=10_000)
        treatment = _rows("treatment", 100, inp=9_000, before=10_000, after=9_000)
        text = format_report(compare(control + treatment))
        assert "gross" in text and "net" in text

    def test_an_unscored_result_names_the_threshold(self):
        text = format_report(compare(_rows("control", 2) + _rows("treatment", 2)))
        assert str(MIN_PER_ARM) in text
