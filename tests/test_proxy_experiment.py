"""Randomized holdout: the only route from an estimated saving to a measured one.

Every other figure memor reports is observational. Compression runs on every
eligible payload, so there is no counterfactual and the "saving" is a tokenizer
estimate of a quantity nobody billed.
"""
from __future__ import annotations

import pytest

from memor.proxy.experiment import (
    ARM_CONTROL,
    ARM_TREATMENT,
    DEFAULT_HOLDOUT_FRACTION,
    assign_arm,
    holdout_fraction,
    is_enabled,
)


def test_the_experiment_is_off_by_default(monkeypatch) -> None:
    """An experiment nobody switched on is one that quietly costs them tokens."""
    monkeypatch.delenv("MEMOR_HOLDOUT_FRACTION", raising=False)

    assert holdout_fraction() == 0.0
    assert not is_enabled()
    assert assign_arm("any payload") == ARM_TREATMENT


def test_assignment_is_stable_for_the_same_payload() -> None:
    """An agent resends the same tool output on every step of a session.

    A fresh coin flip per request would compress a payload in one request and
    hold it out in the next, rewriting the cached prefix each time and charging
    the experiment for cache writes that measure nothing.
    """
    arms = {assign_arm("the same tool output", fraction=0.5) for _ in range(50)}

    assert len(arms) == 1


def test_assignment_hits_the_requested_rate() -> None:
    """A biased split would make the two arms incomparable."""
    arms = [assign_arm(f"payload-{i}", fraction=0.1) for i in range(20_000)]

    observed = arms.count(ARM_CONTROL) / len(arms)

    assert 0.09 < observed < 0.11


def test_different_payloads_land_in_different_arms() -> None:
    arms = {assign_arm(f"payload-{i}", fraction=0.5) for i in range(100)}

    assert arms == {ARM_CONTROL, ARM_TREATMENT}


@pytest.mark.parametrize("raw", ["", "  ", "not-a-number", "0", "-0.5"])
def test_unusable_settings_leave_the_experiment_off(monkeypatch, raw) -> None:
    """Failing open to "no experiment" keeps a typo from withholding savings."""
    monkeypatch.setenv("MEMOR_HOLDOUT_FRACTION", raw)

    assert holdout_fraction() == 0.0
    assert not is_enabled()


def test_a_fraction_above_one_is_clamped(monkeypatch) -> None:
    monkeypatch.setenv("MEMOR_HOLDOUT_FRACTION", "5")

    assert holdout_fraction() == 1.0
    assert assign_arm("anything") == ARM_CONTROL


def test_the_default_fraction_is_a_tenth() -> None:
    """The holdout share is the savings deliberately given up to measure."""
    assert DEFAULT_HOLDOUT_FRACTION == 0.1


def test_pipeline_leaves_held_out_payloads_uncompressed(tmp_path) -> None:
    """The control arm must reach the provider byte-identical."""
    from memor.proxy.pipeline import run_pipeline
    from memor.store.sqlite_store import SqliteStore

    store = SqliteStore(str(tmp_path / "m.db"), dim=256)
    log = "\n".join(f"2026-09-07 12:00:{i:02d} INFO worker={i} ok" for i in range(300))
    body = {
        "model": "claude-3",
        "messages": [
            {"role": "user", "content": "run it"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t", "name": "Bash",
                 "input": {"command": "pytest"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t", "content": log}]},
        ],
    }

    result = run_pipeline("anthropic", body, store, held_out=lambda _text: True)

    assert result.holdout_payloads == 1
    # Held out means untouched: the tokens are counted, not removed.
    assert result.tokens_before == result.tokens_after
    # And the payload reaches the provider byte-identical, which is what makes
    # it a control rather than a differently-compressed request.
    forwarded = result.body["messages"][-1]["content"][0]["content"]
    assert forwarded == log


def test_pipeline_compresses_when_not_held_out(tmp_path) -> None:
    from memor.proxy.pipeline import run_pipeline
    from memor.store.sqlite_store import SqliteStore

    store = SqliteStore(str(tmp_path / "m.db"), dim=256)
    log = "\n".join(f"2026-09-07 12:00:{i:02d} INFO worker={i} ok" for i in range(300))
    body = {
        "model": "claude-3",
        "messages": [
            {"role": "user", "content": "run it"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t", "name": "Bash",
                 "input": {"command": "pytest"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t", "content": log}]},
        ],
    }

    result = run_pipeline("anthropic", body, store, held_out=lambda _text: False)

    assert result.holdout_payloads == 0
    assert result.tokens_after < result.tokens_before


def test_a_request_with_nothing_to_compress_is_not_a_control(tmp_path, monkeypatch) -> None:
    """Padding the control arm with untreatable requests would bias the result.

    A request that carried no compressible payload would have been identical
    under either arm, so counting it as a control adds noise that can only
    shrink the measured effect.
    """
    monkeypatch.setenv("MEMOR_HOLDOUT_FRACTION", "1.0")
    from memor.embed.fake import FakeEmbedder
    from memor.proxy.shim import prepare_request_body
    from memor.store.sqlite_store import SqliteStore

    db = str(tmp_path / "m.db")
    store = SqliteStore(db, dim=256)
    body = {"model": "claude-3", "messages": [{"role": "user", "content": "hello"}]}

    result = prepare_request_body(
        "anthropic", body, store, db_path=db, embedder=FakeEmbedder(dim=256),
        project="p", agent="claude",
    )

    assert result.experiment_arm != ARM_CONTROL


def test_the_ledger_keeps_the_arm(tmp_path) -> None:
    """NULL has to stay distinguishable from the compressed arm."""
    from memor.store.sqlite_store import SqliteStore

    store = SqliteStore(str(tmp_path / "m.db"), dim=256)
    store.record_proxy_savings({
        "timestamp": 1.0, "agent": "claude", "provider": "anthropic",
        "session_id": "s", "tokens_before": 100, "tokens_after": 40,
        "content_types": {}, "passthrough": 0, "experiment_arm": ARM_CONTROL,
    })
    store.record_proxy_savings({
        "timestamp": 2.0, "agent": "claude", "provider": "anthropic",
        "session_id": "s", "tokens_before": 100, "tokens_after": 40,
        "content_types": {}, "passthrough": 0,
    })

    arms = [r[0] for r in store.db.execute(
        "SELECT experiment_arm FROM proxy_savings ORDER BY timestamp")]

    assert arms == [ARM_CONTROL, None]


# --- reading the arms back: the measured result ------------------------------


def _arm_row(arm, inp, out, before=1000, after=400):
    import json
    return {
        "agent": "claude", "tokens_before": before, "tokens_after": after,
        "passthrough": 0, "content_types": json.dumps({"log": 1}),
        "experiment_arm": arm, "upstream_input_tokens": inp,
        "upstream_cache_read_tokens": 100,
        "upstream_cache_creation_tokens": 10, "upstream_output_tokens": out,
    }


def _measured(rows):
    from memor.compression_worth import format_report, summarize_savings
    return "\n".join(format_report(summarize_savings(rows)))


def test_a_real_saving_is_reported_as_measured():
    """The only causal number memor can produce."""
    from memor.compression_worth import CompressionSummary

    n = CompressionSummary.ARM_MIN_REQUESTS
    rows = [_arm_row(ARM_TREATMENT, 400, 200) for _ in range(n)]
    rows += [_arm_row(ARM_CONTROL, 1000, 200) for _ in range(n)]

    text = _measured(rows)

    assert "MEASURED (randomized holdout)" in text
    assert "cost 29.7% less than held-out ones" in text


def test_output_expansion_can_make_the_measured_result_negative():
    """arXiv:2603.23527's failure mode, which an input-only ledger cannot see.

    Input falls from 1000 to 400 while the answer grows from 200 to 2000
    tokens. Every input-side figure reports a saving; priced with output at 5x,
    compression is losing badly.
    """
    from memor.compression_worth import CompressionSummary

    n = CompressionSummary.ARM_MIN_REQUESTS
    rows = [_arm_row(ARM_TREATMENT, 400, 2000) for _ in range(n)]
    rows += [_arm_row(ARM_CONTROL, 1000, 200) for _ in range(n)]

    text = _measured(rows)

    assert "MORE than held-out ones" in text
    assert "VERDICT: compression is costing money" in text


def test_thin_arms_report_how_many_more_are_needed():
    # Above the report's own 25-request floor, below the 57-per-arm one, so
    # the arm message is what the reader sees rather than the generic one.
    rows = [_arm_row(ARM_TREATMENT, 400, 200) for _ in range(20)]
    rows += [_arm_row(ARM_CONTROL, 1000, 200) for _ in range(20)]

    text = _measured(rows)

    assert "per arm are needed" in text
    assert "remain estimates, not measurements" in text


def test_no_experiment_produces_no_measured_section():
    import json
    rows = [{"agent": "claude", "tokens_before": 1000, "tokens_after": 400,
             "passthrough": 0, "content_types": json.dumps({"log": 1})}
            for _ in range(60)]

    assert "MEASURED (randomized holdout)" not in _measured(rows)


def test_the_loader_selects_the_arm_and_output_columns(tmp_path):
    """Both columns were written to the ledger and never read back."""
    from memor.compression_worth import load_savings_rows
    from memor.store.sqlite_store import SqliteStore

    db = str(tmp_path / "m.db")
    store = SqliteStore(db, dim=256)
    store.record_proxy_savings({
        "timestamp": __import__("time").time(), "agent": "claude",
        "provider": "anthropic", "session_id": "s", "tokens_before": 100,
        "tokens_after": 40, "content_types": {}, "passthrough": 0,
        "experiment_arm": ARM_CONTROL, "upstream_output_tokens": 250,
    })

    (row,) = load_savings_rows(db, days=1)

    assert row["experiment_arm"] == ARM_CONTROL
    assert row["upstream_output_tokens"] == 250
