"""`memor hook-worth`: the published figures, re-derivable on demand.

Two numbers this project published described the wrong population -- a "96.5%
saving" that was its own test fixtures, and a rate whose denominator was 70%
Read output the hook never receives. Both survived because the measurement was
a throwaway script nobody could rerun. These tests cover the command that
replaces it, with most attention on the denominator rather than the ratio.
"""
from __future__ import annotations

import json
from pathlib import Path

from memor.eval.hook_worth import format_report, measure


def _session(path: Path, entries: list[tuple[str, str]]) -> None:
    """Write a transcript where each entry is (tool_name, output)."""
    lines = []
    for i, (tool, output) in enumerate(entries):
        tool_id = f"toolu_{i}"
        lines.append(json.dumps({
            "message": {"content": [
                {"type": "tool_use", "id": tool_id, "name": tool,
                 "input": {}},
            ]},
        }))
        lines.append(json.dumps({
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": tool_id,
                 "content": output},
            ]},
        }))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def _noisy_log(lines: int = 400) -> str:
    return "\n".join(f"INFO worker{i % 5}: handled job {i} in {i % 30}ms"
                     for i in range(lines)) + "\nERROR boom\n"


def _source(defs: int = 90) -> str:
    return "\n".join(
        f"def handler_{i}(request):\n    return process(request, {i})\n"
        for i in range(defs))


def test_read_output_is_excluded_from_the_denominator(tmp_path):
    """The mistake that produced a published figure describing the wrong set.

    The hook matches on Bash alone. Counting Read results as uncovered
    measures a limitation it does not have, and understates it for declining
    exactly what it was designed to decline.
    """
    _session(tmp_path / "s.jsonl", [
        ("Bash", _noisy_log()),
        ("Read", _noisy_log()),
        ("Read", _noisy_log()),
        ("Grep", _noisy_log()),
    ])
    result = measure(tmp_path)
    assert result.payloads == 1, "only the Bash result should count"
    assert result.compressed == 1


def test_source_output_counts_as_guarded_not_as_failure(tmp_path):
    _session(tmp_path / "s.jsonl", [
        ("Bash", _noisy_log()),
        ("Bash", _source()),
    ])
    result = measure(tmp_path)
    assert result.payloads == 2
    assert result.compressed == 1
    assert result.guarded == 1
    assert result.guarded_tokens > 0


def test_small_payloads_are_not_counted(tmp_path):
    """Below the hook's own floor, a payload was never a candidate."""
    _session(tmp_path / "s.jsonl", [("Bash", "ok\n"), ("Bash", _noisy_log())])
    assert measure(tmp_path).payloads == 1


def test_rates_use_the_denominators_they_claim(tmp_path):
    _session(tmp_path / "s.jsonl", [
        ("Bash", _noisy_log()),
        ("Bash", _source()),
    ])
    result = measure(tmp_path)

    # "When it fires" is over compressed payloads only.
    assert result.rate_when_firing == (
        result.saved / result.compressed_before * 100)
    # "Overall" is over every large Bash payload, including the guarded one.
    assert result.rate_overall == result.saved / result.tokens * 100
    assert result.rate_overall < result.rate_when_firing
    assert result.coverage == 50.0


def test_report_states_the_denominator_not_just_the_ratio(tmp_path):
    _session(tmp_path / "s.jsonl", [("Bash", _noisy_log()) for _ in range(3)])
    text = "\n".join(format_report(measure(tmp_path)))
    assert "large Bash results" in text
    assert "WHEN IT FIRES" in text
    assert "ACROSS ALL BASH OUTPUT" in text
    # The exclusion must be stated, or a reader will assume it is coverage lost.
    assert "Read, Grep and Glob results are excluded" in text


def test_empty_corpus_reports_nothing_rather_than_zero_percent(tmp_path):
    text = "\n".join(format_report(measure(tmp_path)))
    assert "No large Bash results" in text
    assert "0.0%" not in text


def test_missing_session_directory_is_not_an_error(tmp_path):
    result = measure(tmp_path / "does-not-exist")
    assert result.payloads == 0


def test_sessions_are_selected_by_recency(tmp_path):
    """Selecting by path order picks an arbitrary subset, not recent work.

    Two measurements of this feature disagreed by 139 payloads because one
    listing included subagent transcripts and the other did not; both were
    taking "the last 60 by name".
    """
    import os

    old = tmp_path / "zzz-old.jsonl"
    new = tmp_path / "aaa-new.jsonl"
    _session(old, [("Bash", _noisy_log())])
    _session(new, [("Bash", _noisy_log()), ("Bash", _noisy_log())])
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))

    # Sorting by name would keep "zzz-old"; recency must keep "aaa-new".
    result = measure(tmp_path, limit=1)
    assert result.payloads == 2, "the newest transcript should be the one kept"


def test_subagent_transcripts_are_included(tmp_path):
    """They are real Bash output and were silently in one sample, not another."""
    _session(tmp_path / "main.jsonl", [("Bash", _noisy_log())])
    _session(tmp_path / "sub" / "agent-1.jsonl", [("Bash", _noisy_log())])
    assert measure(tmp_path).payloads == 2


def test_malformed_transcript_lines_are_skipped(tmp_path):
    path = tmp_path / "s.jsonl"
    _session(path, [("Bash", _noisy_log())])
    path.write_text("not json\n" + path.read_text() + "\n{broken")
    assert measure(tmp_path).payloads == 1
