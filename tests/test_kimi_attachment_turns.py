"""A Kimi turn carrying an attachment must parse, not crash the daemon forever.

Kimi sends ``user_input`` as a plain string for a typed message, but as a list
of content blocks when the turn carries an attachment. String methods on that
list raised, and since the daemon records a file's state only after a
successful parse, the affected sessions were retried on every 30s poll
indefinitely -- each retry re-running the whole post-ingest pipeline, which is
what pinned the daemon at ~90% CPU.
"""
from __future__ import annotations

import json

from memor.ingest.kimi import _user_input_text, parse_wire


def _wire(tmp_path, records):
    path = tmp_path / "wire.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records))
    return path


def _turn(user_input):
    return {"message": {"type": "TurnBegin", "payload": {"user_input": user_input}}}


def test_flattens_a_plain_string():
    assert _user_input_text("hello") == "hello"


def test_flattens_content_blocks_and_drops_images():
    """An image has no text to embed, and its base64 would be megabytes of noise."""
    value = [
        {"type": "text", "text": "look at this screenshot"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    out = _user_input_text(value)
    assert out == "look at this screenshot"
    assert "base64" not in out


def test_unknown_shapes_are_empty_not_fatal():
    assert _user_input_text(None) == ""
    assert _user_input_text({"unexpected": "dict"}) == ""
    assert _user_input_text(12345) == ""


def test_parse_wire_survives_a_list_user_input(tmp_path):
    """The real failure: this raised AttributeError and aborted the file."""
    path = _wire(tmp_path, [
        _turn([{"type": "text", "text": "a message with an attachment"},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}}]),
        _turn("a plain message"),
    ])
    arts = parse_wire(path, project="p", filter_noise=False)
    texts = " ".join(a.text for a in arts)
    assert "attachment" in texts
    assert "plain message" in texts


def test_content_part_with_non_string_text_is_skipped(tmp_path):
    path = _wire(tmp_path, [
        {"message": {"type": "ContentPart", "payload": {"type": "text", "text": ["not", "a", "string"]}}},
        _turn("a plain message"),
    ])
    arts = parse_wire(path, project="p", filter_noise=False)
    assert any("plain message" in a.text for a in arts)
