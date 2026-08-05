"""Tool-result provenance: which tool produced a payload, for which file, when.

Without this the proxy can only see the newest payload, which is both the
smallest share of the context and the one the agent is most likely about to act
on — the worst possible compression target.
"""
from __future__ import annotations

import json

from memor.proxy.adapters import (
    extract_all_tool_payloads,
    extract_latest_tool_payloads,
)


def _use(uid, name="Read", **inp):
    return {"type": "tool_use", "id": uid, "name": name, "input": inp}


def _result(uid, text):
    return {"type": "tool_result", "tool_use_id": uid, "content": text}


def _anthropic_body(pairs):
    """pairs: list of (tool_use block, result text)."""
    messages = []
    for use, text in pairs:
        messages.append({"role": "assistant", "content": [use]})
        messages.append({"role": "user", "content": [_result(use["id"], text)]})
    return {"messages": messages}


# --- provenance --------------------------------------------------------------


def test_anthropic_payload_carries_tool_name_and_file():
    body = _anthropic_body([(_use("t1", file_path="/a/b.py"), "contents")])
    p = extract_all_tool_payloads("anthropic", body)[0]
    assert p.tool_name == "Read"
    assert p.file_path == "/a/b.py"
    assert p.text == "contents"


def test_all_payloads_walks_every_message_not_just_the_last():
    body = _anthropic_body([
        (_use("t1", file_path="/a.py"), "first"),
        (_use("t2", file_path="/b.py"), "second"),
        (_use("t3", file_path="/c.py"), "third"),
    ])
    assert len(extract_all_tool_payloads("anthropic", body)) == 3
    # The old extractor sees only the final turn — that is the bug being fixed.
    assert len(extract_latest_tool_payloads("anthropic", body)) == 1


def test_unresolvable_origin_still_yields_a_payload():
    """A tool_result with no matching tool_use must not be dropped."""
    body = {"messages": [{"role": "user", "content": [_result("missing", "text")]}]}
    p = extract_all_tool_payloads("anthropic", body)[0]
    assert p.text == "text"
    assert p.file_path is None
    # Unknown origin is treated as latest so a recency rule can never touch it.
    assert p.is_latest_for_file is True


def test_alternate_file_argument_keys_resolve():
    for key in ("file_path", "notebook_path", "path", "filePath"):
        body = _anthropic_body([(_use("t1", **{key: "/x.py"}), "c")])
        assert extract_all_tool_payloads("anthropic", body)[0].file_path == "/x.py"


def test_list_content_blocks_are_flattened():
    body = {"messages": [
        {"role": "assistant", "content": [_use("t1", file_path="/a.py")]},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "t1",
            "content": [{"type": "text", "text": "aa"}, {"type": "text", "text": "bb"}],
        }]},
    ]}
    assert extract_all_tool_payloads("anthropic", body)[0].text == "aabb"


# --- recency, which is what makes compression safe ---------------------------


def test_only_the_newest_read_of_a_file_is_latest():
    body = _anthropic_body([
        (_use("t1", file_path="/a.py"), "v1"),
        (_use("t2", file_path="/a.py"), "v2"),
    ])
    payloads = extract_all_tool_payloads("anthropic", body)
    assert [p.is_latest_for_file for p in payloads] == [False, True]


def test_recency_is_tracked_per_file_not_globally():
    """An older read of one file must not be marked latest by a newer read of another."""
    body = _anthropic_body([
        (_use("t1", file_path="/a.py"), "a1"),
        (_use("t2", file_path="/b.py"), "b1"),
    ])
    payloads = extract_all_tool_payloads("anthropic", body)
    assert all(p.is_latest_for_file for p in payloads)


def test_interleaved_rereads_resolve_correctly():
    body = _anthropic_body([
        (_use("t1", file_path="/a.py"), "a1"),
        (_use("t2", file_path="/b.py"), "b1"),
        (_use("t3", file_path="/a.py"), "a2"),
    ])
    by_file = {}
    for p in extract_all_tool_payloads("anthropic", body):
        by_file.setdefault(p.file_path, []).append(p.is_latest_for_file)
    assert by_file["/a.py"] == [False, True]
    assert by_file["/b.py"] == [True]


def test_message_index_is_recorded_in_order():
    body = _anthropic_body([
        (_use("t1", file_path="/a.py"), "a"),
        (_use("t2", file_path="/b.py"), "b"),
    ])
    idx = [p.message_index for p in extract_all_tool_payloads("anthropic", body)]
    assert idx == sorted(idx)


# --- openai ------------------------------------------------------------------


def _openai_body(calls):
    messages = []
    for cid, name, args, text in calls:
        messages.append({"role": "assistant", "tool_calls": [
            {"id": cid, "function": {"name": name, "arguments": json.dumps(args)}}
        ]})
        messages.append({"role": "tool", "tool_call_id": cid, "content": text})
    return {"messages": messages}


def test_openai_provenance_from_tool_calls():
    body = _openai_body([("c1", "read_file", {"file_path": "/a.py"}, "contents")])
    p = extract_all_tool_payloads("openai", body)[0]
    assert p.tool_name == "read_file"
    assert p.file_path == "/a.py"


def test_openai_all_walks_non_contiguous_tool_messages():
    body = _openai_body([
        ("c1", "read_file", {"file_path": "/a.py"}, "one"),
        ("c2", "read_file", {"file_path": "/b.py"}, "two"),
    ])
    assert len(extract_all_tool_payloads("openai", body)) == 2
    # Old extractor only takes the trailing contiguous run.
    assert len(extract_latest_tool_payloads("openai", body)) == 1


def test_openai_malformed_arguments_do_not_raise():
    body = {"messages": [
        {"role": "assistant", "tool_calls": [
            {"id": "c1", "function": {"name": "read_file", "arguments": "{not json"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "text"},
    ]}
    p = extract_all_tool_payloads("openai", body)[0]
    assert p.file_path is None
    assert p.tool_name == "read_file"


# --- safety ------------------------------------------------------------------


def test_extraction_does_not_mutate_the_body():
    body = _anthropic_body([(_use("t1", file_path="/a.py"), "c")])
    before = json.dumps(body, sort_keys=True)
    extract_all_tool_payloads("anthropic", body)
    assert json.dumps(body, sort_keys=True) == before


def test_unknown_provider_returns_nothing():
    assert extract_all_tool_payloads("gemini", {"messages": []}) == []


def test_empty_body_is_safe():
    assert extract_all_tool_payloads("anthropic", {}) == []
    assert extract_all_tool_payloads("openai", {}) == []
