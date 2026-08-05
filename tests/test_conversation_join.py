"""Joining a proxy-served recall to the episode it belonged to.

The proxy and the episode meter see the same conversation from opposite ends
and share no identifier: Claude Code sends no session header, and the meter
knows a session only as a filename. Without a join, proxy recalls are invisible
to the meter and land in the *control* arm — which manufactures a false "no
effect", the one verdict that looks like an honest null.
"""
from __future__ import annotations

import json

import pytest

from memor.conversation import conversation_key, key_from_body
from memor.episodes import Episode, attach_logged_recalls, parse_episodes


# --- both sides must agree ----------------------------------------------------


def test_body_and_transcript_agree_on_the_key(tmp_path):
    """The whole point: two components, no coordination, same answer."""
    opening = "Please refactor the retry logic in the client."

    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": opening}]}]}
    from_proxy = key_from_body(body)

    transcript = tmp_path / "s.jsonl"
    transcript.write_text(json.dumps({
        "type": "user", "timestamp": 1000.0,
        "message": {"role": "user", "content": opening},
    }) + "\n")
    from_meter = parse_episodes(transcript)[0].conversation_key

    assert from_proxy == from_meter != ""


def test_whitespace_differences_do_not_break_the_join():
    """The same message arrives as JSON one side and a record the other."""
    assert conversation_key("do the thing") == conversation_key("  do   the\nthing  ")


def test_different_openings_get_different_keys():
    assert conversation_key("fix the parser") != conversation_key("fix the proxy")


def test_an_empty_opening_yields_no_key():
    assert conversation_key("") == ""
    assert conversation_key("   ") == ""
    assert key_from_body({"messages": []}) == ""
    assert key_from_body(None) == ""


def test_the_key_comes_from_the_first_user_turn_not_the_latest():
    """A conversation keeps its identity as it grows."""
    first = {"role": "user", "content": "the opening question"}
    body_early = {"messages": [first]}
    body_later = {"messages": [first, {"role": "assistant", "content": "ok"},
                               {"role": "user", "content": "a later question"}]}
    assert key_from_body(body_early) == key_from_body(body_later)


def test_a_huge_opening_message_is_still_cheap():
    key = conversation_key("x" * 5_000_000)
    assert len(key) == 16


# --- attributing recalls to episodes -----------------------------------------


def _episodes(*starts) -> list[Episode]:
    return [Episode(started_at=t) for t in starts]


def test_a_recall_marks_the_episode_it_falls_inside():
    eps = _episodes(100.0, 200.0, 300.0)
    attach_logged_recalls(eps, [(205.0, 400)])
    assert [e.had_recall for e in eps] == [False, True, False]


def test_a_recall_at_the_exact_start_belongs_to_that_episode():
    eps = _episodes(100.0, 200.0)
    attach_logged_recalls(eps, [(200.0, 50)])
    assert eps[1].had_recall is True


def test_a_recall_before_any_episode_is_dropped_not_guessed():
    eps = _episodes(100.0)
    attach_logged_recalls(eps, [(50.0, 400)])
    assert eps[0].had_recall is False


def test_several_recalls_in_one_episode_accumulate():
    eps = _episodes(100.0)
    attach_logged_recalls(eps, [(110.0, 300), (120.0, 200)])
    assert eps[0].had_recall is True
    assert eps[0].recall_chars == 500


def test_episodes_out_of_order_are_handled():
    eps = _episodes(300.0, 100.0, 200.0)
    attach_logged_recalls(eps, [(205.0, 10)])
    assert eps[2].had_recall is True


def test_an_already_marked_episode_is_not_disturbed():
    """A hook-served episode stays marked when the ledger says nothing."""
    eps = [Episode(started_at=100.0, had_recall=True, recall_chars=900)]
    attach_logged_recalls(eps, [])
    assert eps[0].had_recall is True and eps[0].recall_chars == 900


def test_no_recalls_and_no_episodes_are_both_safe():
    attach_logged_recalls([], [(1.0, 1)])
    attach_logged_recalls(_episodes(1.0), [])


# --- the regression this exists to prevent -----------------------------------


def test_a_proxied_episode_is_not_counted_as_a_control(tmp_path):
    """Before the join, this episode looked untreated and polluted the control
    arm. The transcript deliberately contains no recall attachment at all."""
    opening = "why is the build failing"
    transcript = tmp_path / "s.jsonl"
    transcript.write_text("\n".join([
        json.dumps({"type": "user", "timestamp": 1000.0,
                    "message": {"role": "user", "content": opening}}),
        json.dumps({"type": "assistant", "timestamp": 1001.0,
                    "message": {"role": "assistant", "content": [],
                                "usage": {"input_tokens": 10}}}),
    ]) + "\n")

    eps = parse_episodes(transcript)
    assert eps[0].had_recall is False, "no transcript evidence, as expected"

    attach_logged_recalls(eps, [(1000.5, 531)])
    assert eps[0].had_recall is True
    assert eps[0].conversation_key == key_from_body(
        {"messages": [{"role": "user", "content": opening}]})
