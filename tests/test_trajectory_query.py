"""Trajectory-derived retrieval queries.

Three properties matter and are pinned here: the signal is actually extracted,
the prompt is never damaged when anything goes wrong, and the work stays inside
the recall hot path's budget no matter how long the session has run.
"""
from __future__ import annotations

import json
import time

import pytest

from memor.trajectory import (
    MAX_APPENDED_CHARS,
    TrajectorySignals,
    build_query,
    enrich_from_transcript,
    extract_signals,
    read_tail,
)


def _rec(role: str, blocks: list) -> str:
    return json.dumps({"type": role, "message": {"role": role, "content": blocks}})


def _use(tool: str, params: dict) -> dict:
    return {"type": "tool_use", "id": "t1", "name": tool, "input": params}


def _result(text: str) -> dict:
    return {"type": "tool_result", "tool_use_id": "t1", "content": text}


@pytest.fixture
def transcript(tmp_path):
    def _write(records: list[str]):
        p = tmp_path / "session.jsonl"
        p.write_text("\n".join(records) + "\n")
        return p
    return _write


# --- the signal --------------------------------------------------------------


def test_recent_file_paths_are_extracted(transcript):
    p = transcript([
        _rec("assistant", [_use("Read", {"file_path": "/repo/memor/recall.py"})]),
        _rec("assistant", [_use("Edit", {"file_path": "/repo/memor/store/sqlite_store.py"})]),
    ])
    signals = extract_signals(p)
    assert "memor/recall.py" in signals.files
    assert "store/sqlite_store.py" in signals.files


def test_most_recent_file_comes_first(transcript):
    p = transcript([
        _rec("assistant", [_use("Read", {"file_path": "/repo/old.py"})]),
        _rec("assistant", [_use("Read", {"file_path": "/repo/new.py"})]),
    ])
    assert extract_signals(p).files[0] == "repo/new.py"


def test_duplicate_paths_appear_once(transcript):
    p = transcript([
        _rec("assistant", [_use("Read", {"file_path": "/repo/a.py"})]),
        _rec("assistant", [_use("Edit", {"file_path": "/repo/a.py"})]),
    ])
    assert extract_signals(p).files.count("repo/a.py") == 1


def test_the_latest_error_is_captured(transcript):
    p = transcript([
        _rec("user", [_result("ModuleNotFoundError: No module named 'sqlite_vec'")]),
    ])
    assert "sqlite_vec" in extract_signals(p).error


def test_a_traceback_reports_its_last_line_not_its_first(transcript):
    """"Traceback (most recent call last):" names nothing."""
    blob = ("Traceback (most recent call last):\n"
            '  File "/repo/x.py", line 3, in <module>\n'
            "    boom()\n"
            "ZeroDivisionError: division by zero")
    p = transcript([_rec("user", [_result(blob)])])
    error = extract_signals(p).error
    assert "ZeroDivisionError" in error
    assert not error.lower().startswith("traceback")


def test_ordinary_output_is_not_mistaken_for_an_error(transcript):
    p = transcript([_rec("user", [_result("All 628 tests passed in 4.2s")])])
    assert extract_signals(p).error == ""


def test_only_the_first_error_encountered_walking_back_is_kept(transcript):
    p = transcript([
        _rec("user", [_result("ValueError: older failure")]),
        _rec("user", [_result("KeyError: newer failure")]),
    ])
    assert "newer" in extract_signals(p).error


# --- query assembly ----------------------------------------------------------


def test_the_prompt_stays_first_and_whole():
    prompt = "why is the retriever returning stale hits"
    out = build_query(prompt, TrajectorySignals(files=["memor/recall.py"], error="boom"))
    assert out.startswith(prompt)


def test_signals_are_appended_not_substituted():
    out = build_query("fix it", TrajectorySignals(files=["a/b.py"], error="TypeError: x"))
    assert "a/b.py" in out and "TypeError" in out


def test_no_signal_returns_the_prompt_unchanged():
    assert build_query("fix it", TrajectorySignals()) == "fix it"


def test_a_long_error_cannot_crowd_out_the_file_list():
    signals = TrajectorySignals(files=["memor/recall.py"], error="E" * 5000)
    out = build_query("fix it", signals)
    assert "memor/recall.py" in out
    assert len(out) <= len("fix it | ") + MAX_APPENDED_CHARS


# --- failing safe ------------------------------------------------------------


def test_a_missing_transcript_returns_the_prompt(monkeypatch):
    monkeypatch.setenv("MEMOR_TRAJECTORY_QUERY", "1")
    assert enrich_from_transcript("hello", "/nope/missing.jsonl") == "hello"


def test_no_transcript_path_returns_the_prompt(monkeypatch):
    monkeypatch.setenv("MEMOR_TRAJECTORY_QUERY", "1")
    assert enrich_from_transcript("hello", None) == "hello"


def test_corrupt_lines_are_skipped_not_fatal(transcript, monkeypatch):
    monkeypatch.setenv("MEMOR_TRAJECTORY_QUERY", "1")
    p = transcript([
        "{not json at all",
        _rec("assistant", [_use("Read", {"file_path": "/repo/good.py"})]),
    ])
    assert "repo/good.py" in enrich_from_transcript("hello", p)


def test_records_shaped_differently_are_ignored(transcript, monkeypatch):
    """Another agent's transcript must degrade to "no signal", not an error."""
    monkeypatch.setenv("MEMOR_TRAJECTORY_QUERY", "1")
    p = transcript([json.dumps({"event": "something", "payload": [1, 2, 3]})])
    assert enrich_from_transcript("hello", p) == "hello"


def test_disabled_by_default(transcript, monkeypatch):
    monkeypatch.delenv("MEMOR_TRAJECTORY_QUERY", raising=False)
    p = transcript([_rec("assistant", [_use("Read", {"file_path": "/repo/a.py"})])])
    assert enrich_from_transcript("hello", p) == "hello"


# --- the hot path ------------------------------------------------------------


def test_a_huge_transcript_costs_the_same_as_a_small_one(tmp_path):
    """Cost must be bounded by the byte cap, not by session length."""
    big = tmp_path / "big.jsonl"
    filler = _rec("assistant", [_use("Read", {"file_path": "/repo/filler.py"})])
    with big.open("w") as fh:
        for _ in range(40_000):
            fh.write(filler + "\n")
        fh.write(_rec("assistant", [_use("Read", {"file_path": "/repo/target.py"})]) + "\n")

    assert big.stat().st_size > 5_000_000

    start = time.perf_counter()
    signals = extract_signals(big)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert "repo/target.py" in signals.files, "the tail window missed the newest record"
    assert elapsed_ms < 15, f"took {elapsed_ms:.1f}ms of a 15ms recall budget"


def test_the_tail_window_never_yields_a_partial_line(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps({"n": i, "pad": "x" * 200}) for i in range(500)))
    for line in read_tail(p, max_bytes=1024):
        json.loads(line)  # raises if a fragment survived


def test_a_short_file_is_read_whole(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"n": 1}\n{"n": 2}\n')
    assert len(read_tail(p, max_bytes=1_000_000)) == 2
