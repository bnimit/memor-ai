"""Episode-level accounting for recall."""
from __future__ import annotations

import json

from memor.episodes import (
    Episode,
    is_user_prompt,
    memor_recall_chars,
    parse_episodes,
    stratified_deltas,
    summarize,
    verdict,
)


def _user_text(text="do the thing", ts="2026-08-03T10:00:00Z"):
    return {"type": "user", "timestamp": ts, "message": {"content": [{"type": "text", "text": text}]}}


def _tool_result():
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": "out"}]}}


def _assistant(tools=0, out=100, cache_read=0):
    content = [{"type": "tool_use", "name": "Read"} for _ in range(tools)]
    return {
        "type": "assistant",
        "timestamp": "2026-08-03T10:00:01Z",
        "message": {
            "content": content,
            "usage": {
                "input_tokens": 10,
                "output_tokens": out,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": 0,
            },
        },
    }


def _recall(chars=500):
    return {
        "type": "attachment",
        "attachment": {
            "type": "hook_additional_context",
            "hookName": "UserPromptSubmit",
            "content": "## Recalled Memories (project: p)\n" + ("x" * chars),
        },
    }


def _other_hook():
    return {
        "type": "attachment",
        "attachment": {
            "type": "hook_additional_context",
            "hookName": "SessionStart",
            "content": "You have superpowers.",
        },
    }


def _write(tmp_path, records):
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records))
    return p


# --- the parsing distinction that makes this measurable ----------------------


def test_tool_results_do_not_open_an_episode():
    """Tool results are `type: user`; counting them makes every loop step a turn."""
    assert is_user_prompt(_user_text()) is True
    assert is_user_prompt(_tool_result()) is False


def test_plain_string_prompt_counts():
    assert is_user_prompt({"type": "user", "message": {"content": "hello"}}) is True


def test_empty_prompt_does_not_count():
    assert is_user_prompt({"type": "user", "message": {"content": "  "}}) is False


def test_only_memor_recall_attachments_count():
    assert memor_recall_chars(_recall()) > 0
    assert memor_recall_chars(_other_hook()) == 0
    assert memor_recall_chars(_assistant()) == 0


# --- episode assembly --------------------------------------------------------


def test_agent_loop_collapses_into_one_episode(tmp_path):
    records = [
        _user_text(),
        _assistant(tools=1),
        _tool_result(),
        _assistant(tools=1),
        _tool_result(),
        _assistant(tools=0),
    ]
    episodes = parse_episodes(_write(tmp_path, records), project="p")
    assert len(episodes) == 1
    assert episodes[0].tool_calls == 2
    assert episodes[0].assistant_steps == 3


def test_second_prompt_starts_a_new_episode(tmp_path):
    records = [_user_text("a"), _assistant(tools=3), _user_text("b"), _assistant(tools=1)]
    episodes = parse_episodes(_write(tmp_path, records), project="p")
    assert [e.tool_calls for e in episodes] == [3, 1]


def test_recall_is_attributed_to_its_episode(tmp_path):
    records = [
        _user_text("a"),
        _recall(300),
        _assistant(tools=1),
        _user_text("b"),
        _assistant(tools=1),
    ]
    episodes = parse_episodes(_write(tmp_path, records), project="p")
    assert episodes[0].had_recall is True
    assert episodes[0].recall_chars > 300
    assert episodes[1].had_recall is False


def test_tokens_accumulate_across_the_episode(tmp_path):
    records = [_user_text(), _assistant(out=100, cache_read=1000), _tool_result(),
               _assistant(out=50, cache_read=2000)]
    ep = parse_episodes(_write(tmp_path, records), project="p")[0]
    assert ep.output_tokens == 150
    assert ep.cache_read_tokens == 3000
    assert ep.total_tokens == ep.context_tokens + ep.output_tokens


def test_malformed_lines_are_skipped(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(_user_text()) + "\n{ broken\n" + json.dumps(_assistant(tools=1)))
    assert parse_episodes(p, project="p")[0].tool_calls == 1


# --- verdict discipline ------------------------------------------------------


def _eps(n, *, recall, tools, prompt_chars=100):
    return [
        Episode(project="p", had_recall=recall, tool_calls=tools,
                assistant_steps=1, prompt_chars=prompt_chars)
        for _ in range(n)
    ]


def test_small_samples_report_insufficient_data():
    s = summarize(_eps(5, recall=True, tools=1) + _eps(5, recall=False, tools=9))
    assert s["overall"]["verdict"] == "insufficient_data"


def test_sign_flip_across_strata_is_reported_as_no_effect():
    """An aggregate that reverses by prompt length is composition, not causation."""
    with_r = _eps(30, recall=True, tools=2, prompt_chars=30) + _eps(30, recall=True, tools=9, prompt_chars=200)
    without = _eps(30, recall=False, tools=9, prompt_chars=30) + _eps(30, recall=False, tools=2, prompt_chars=200)
    s = summarize(with_r + without)
    assert s["overall"]["verdict"] == "no_effect"


def test_consistent_reduction_is_reported_as_saves():
    with_r = _eps(30, recall=True, tools=2, prompt_chars=30) + _eps(30, recall=True, tools=2, prompt_chars=200)
    without = _eps(30, recall=False, tools=9, prompt_chars=30) + _eps(30, recall=False, tools=9, prompt_chars=200)
    s = summarize(with_r + without)
    assert s["overall"]["verdict"] == "saves"
    assert s["overall"]["tool_call_delta_pct"] > 0


def test_consistent_increase_is_reported_as_costs():
    with_r = _eps(30, recall=True, tools=9, prompt_chars=30) + _eps(30, recall=True, tools=9, prompt_chars=200)
    without = _eps(30, recall=False, tools=2, prompt_chars=30) + _eps(30, recall=False, tools=2, prompt_chars=200)
    s = summarize(with_r + without)
    assert s["overall"]["verdict"] == "costs"


def test_tiny_delta_is_not_called_an_effect():
    with_r = _eps(30, recall=True, tools=10, prompt_chars=30) + _eps(30, recall=True, tools=10, prompt_chars=200)
    without = _eps(30, recall=False, tools=10, prompt_chars=30) + _eps(30, recall=False, tools=10, prompt_chars=200)
    s = summarize(with_r + without)
    assert s["overall"]["verdict"] == "no_effect"


def test_stratified_cells_below_minimum_are_not_scored():
    cells = stratified_deltas(_eps(3, recall=True, tools=1), _eps(3, recall=False, tools=5))
    assert all(c["scored"] is False for c in cells)


def test_verdict_requires_two_scored_strata():
    assert verdict({"scored": True, "tool_call_delta_pct": 50.0}, []) == "insufficient_data"


def test_summary_always_carries_the_confound_note():
    s = summarize([])
    assert "Observational" in s["confound"]
