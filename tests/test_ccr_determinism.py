"""CCR ids must be content-addressed, not random.

A random id means compressing the same payload twice yields different marker
text. Any strategy that rewrites a payload appearing in more than one request —
compressing older turns, which is where the token cost actually lives — would
then change the prompt prefix on every call and miss the cache every time,
costing more than the compression saves.
"""
from __future__ import annotations

from memor.proxy.pipeline import _CCR_ID_LEN, ccr_id_for


def test_same_content_yields_same_id():
    text = "ERROR failed to connect\n" * 50
    assert ccr_id_for(text) == ccr_id_for(text)


def test_different_content_yields_different_id():
    assert ccr_id_for("alpha" * 100) != ccr_id_for("beta" * 100)


def test_single_character_change_changes_the_id():
    a = "line one\nline two\nline three\n"
    b = "line one\nline TWO\nline three\n"
    assert ccr_id_for(a) != ccr_id_for(b)


def test_id_width_matches_the_previous_uuid_hex():
    """Marker width drives token count; changing it would shift savings."""
    assert len(ccr_id_for("anything")) == _CCR_ID_LEN == 32


def test_id_is_hex():
    assert all(c in "0123456789abcdef" for c in ccr_id_for("payload"))


def test_empty_and_unicode_are_handled():
    assert len(ccr_id_for("")) == _CCR_ID_LEN
    assert len(ccr_id_for("día — ✓ 日本語")) == _CCR_ID_LEN


def test_lone_surrogates_do_not_raise():
    """Tool output can carry malformed text; an id is still required."""
    assert len(ccr_id_for("bad \ud800 surrogate")) == _CCR_ID_LEN


def test_id_is_stable_across_calls_in_sequence():
    text = "some tool output\n" * 20
    ids = {ccr_id_for(text) for _ in range(25)}
    assert len(ids) == 1
