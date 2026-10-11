"""The hook's newer reach: lossless Grep and MCP rewrites, and the user's prompt.

Grep and MCP results are rewritten only in ways that lose no content, because
no line-dropping compressor may touch them. Log compression may use the user's
latest prompt, read from the transcript, because the hook compresses a payload
once, before it enters the transcript.
"""
from __future__ import annotations

import json

from memor.compress import compress_text
from memor.posttool_compress import build_response, hook_query, minify_json


def _grep_request(content: str, mode: str = "content") -> dict:
    return {"tool_name": "Grep", "tool_input": {"pattern": "retry"},
            "tool_response": {"mode": mode, "numFiles": 3, "filenames": [],
                              "content": content, "numLines": content.count("\n") + 1}}


def _grep_output() -> str:
    lines = []
    for f in range(3):
        for n in range(1, 16):
            lines.append(f"memor/proxy/very/long/path/module_{f}.py:{n * 7}:"
                         f"        retry_policy = RetryPolicy(attempts={n})")
    return "\n".join(lines)


# --- Grep ---------------------------------------------------------------------

def test_grep_content_is_folded_without_losing_a_match():
    text = _grep_output()
    out = build_response(_grep_request(text))
    updated = out["hookSpecificOutput"]["updatedToolOutput"]
    folded = updated["content"]
    assert len(folded) < len(text)
    for f in range(3):
        assert f"memor/proxy/very/long/path/module_{f}.py:" in folded
        for n in range(1, 16):
            assert f"retry_policy = RetryPolicy(attempts={n})" in folded
            assert str(n * 7) in folded
    # Every other field of the tool's output shape is preserved.
    assert updated["mode"] == "content" and updated["numFiles"] == 3


def test_grep_file_list_mode_is_left_alone():
    """A list of paths is a manifest: the list is the answer."""
    assert build_response(_grep_request(_grep_output(), mode="files_with_matches")) == {}


def test_grep_output_without_line_numbers_is_left_alone():
    text = "\n".join(f"src/m{i}.py:    retry_policy = RetryPolicy()" for i in range(60))
    assert build_response(_grep_request(text)) == {}


def test_short_grep_is_left_alone():
    assert build_response(_grep_request("a.py:1:x\nb.py:2:y")) == {}


# --- MCP ----------------------------------------------------------------------

def _pretty_issues(n: int = 30) -> str:
    return json.dumps({"issues": [{"number": i, "title": f"Issue {i}  with  spaces",
                                    "body": "line one\nline two", "score": 3.141592653589793238}
                                   for i in range(n)]}, indent=2)


def test_mcp_string_result_loses_only_whitespace():
    text = _pretty_issues()
    out = build_response({"tool_name": "mcp__github__list_issues", "tool_response": text})
    updated = out["hookSpecificOutput"]["updatedToolOutput"]
    assert isinstance(updated, str) and len(updated) < len(text)
    assert json.loads(updated) == json.loads(text)
    assert len(json.loads(updated)["issues"]) == 30  # nothing sampled away


def test_mcp_content_blocks_keep_their_shape():
    text = _pretty_issues()
    blocks = [{"type": "text", "text": text}, {"type": "image", "data": "abc"}]
    out = build_response({"tool_name": "mcp__github__list_issues", "tool_response": blocks})
    updated = out["hookSpecificOutput"]["updatedToolOutput"]
    assert [b["type"] for b in updated] == ["text", "image"]
    assert updated[1] == blocks[1]
    assert json.loads(updated[0]["text"]) == json.loads(text)


def test_mcp_prose_and_compact_json_are_left_alone():
    prose = "Here is a long explanation. " * 100
    assert build_response({"tool_name": "mcp__x__y", "tool_response": prose}) == {}
    compact = json.dumps(json.loads(_pretty_issues()), separators=(",", ":"))
    assert build_response({"tool_name": "mcp__x__y", "tool_response": compact}) == {}


def test_minify_is_byte_exact_on_values():
    """A parse-and-dump round trip would round the decimal and merge the keys."""
    src = '{\n  "a": "x  y\\" z",\n  "n": 3.141592653589793238,\n  "k": 1,\n  "k": 2\n}'
    assert minify_json(src) == '{"a":"x  y\\" z","n":3.141592653589793238,"k":1,"k":2}'
    assert minify_json("not json {") is None
    assert minify_json('{"already":"compact"}') is None


# --- the user's prompt --------------------------------------------------------

def _log_with_quiet_cause() -> str:
    lines = [f"2026-10-11T14:{i // 60:02d}:{i % 60:02d}.000Z INFO  http req_id={i:012x} "
             f"method=GET path=/api/orders/{i} status=200 dur_ms=12" for i in range(400)]
    lines.insert(200, "2026-10-11T14:03:20.000Z INFO  config reloaded rev=418 "
                      "changed: db.pool.max_size 50 -> 20")
    return "\n".join(lines)


def test_log_keeps_a_line_naming_what_the_user_asked_about():
    text = _log_with_quiet_cause()
    cause = "changed: db.pool.max_size 50 -> 20"
    assert cause not in compress_text(text).text
    assert cause in compress_text(text, query="Did a config change break this?").text


def test_a_term_on_every_line_keeps_nothing_extra():
    text = _log_with_quiet_cause()
    plain = compress_text(text).text
    # "orders" is on 400 of 401 lines: it picks out nothing, so it is ignored.
    assert compress_text(text, query="why are orders failing").text == plain


def test_query_matches_are_capped():
    lines = [f"2026-10-11T14:00:00.000Z INFO  worker job={i} step=fetch" for i in range(5000)]
    for i in range(0, 5000, 100):  # 50 lines naming "rebalance"
        lines[i] = f"2026-10-11T14:00:00.000Z INFO  worker rebalance partition={i}"
    out = compress_text("\n".join(lines), query="rebalance").text
    assert out.count("rebalance") <= 40 + 2  # cap, plus collapse notes


def test_hook_reads_the_prompt_from_the_transcript(tmp_path):
    transcript = tmp_path / "s.jsonl"
    transcript.write_text("\n".join(json.dumps(r) for r in [
        {"type": "user", "message": {"role": "user", "content": "Did a config change break this?"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "earlier output"}]}},
    ]))
    req = {"tool_name": "Bash", "transcript_path": str(transcript),
           "tool_input": {"command": "tail -n 400 /var/log/app.log", "description": "Read app log"},
           "tool_response": {"stdout": _log_with_quiet_cause(), "stderr": "", "interrupted": False}}
    query = hook_query(req)
    assert "Did a config change break this?" in query and "tail -n 400" in query
    out = build_response(req)
    assert "changed: db.pool.max_size 50 -> 20" in out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]


def test_hook_query_survives_a_missing_transcript():
    req = {"tool_input": {"command": "make test"}, "transcript_path": "/nope/x.jsonl"}
    assert hook_query(req) == "make test"
