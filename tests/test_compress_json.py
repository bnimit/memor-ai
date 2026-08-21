import json
from memor.compress import compress_text

def test_json_array_keeps_structure_shrinks():
    arr = [{"id": i, "ok": True, "blob": "x" * 50} for i in range(40)]
    text = json.dumps(arr)
    r = compress_text(text, content_type="json")
    assert r.tokens_after < r.tokens_before
    assert r.passthrough is False
    # Must remain parseable JSON (no C-style comment trailer).
    parsed = json.loads(r.text)
    assert isinstance(parsed, list)
    assert parsed[-1].get("_memor_note", "").startswith("kept ")


def test_nested_arrays_are_sampled():
    """API payloads nest the big array: {"data": {"results": [...]}}."""
    import json

    from memor.compress import compress_text

    text = json.dumps({"data": {"results": [{"id": i} for i in range(300)]}})
    result = compress_text(text)
    assert result.tokens_after < result.tokens_before * 0.2
    # Output must stay parseable and keep the enclosing shape.
    parsed = json.loads(result.text)
    assert "data" in parsed and "results" in parsed["data"]


def test_nested_sampling_keeps_error_items():
    import json

    from memor.compress import compress_text

    rows = [{"id": i, "status": "ok"} for i in range(300)]
    rows[150] = {"id": 150, "status": "ERROR: disk full"}
    text = json.dumps({"items": rows})
    assert "disk full" in compress_text(text).text


def test_small_arrays_are_untouched():
    import json

    from memor.compress.json_crush import compress_json

    payload = {"items": [{"id": i} for i in range(5)]}
    assert json.loads(compress_json(json.dumps(payload))) == payload


def test_long_string_values_are_truncated():
    """Array sampling alone missed the common shape.

    On a live corpus 39% of the string mass sat in values over 200 characters
    -- a pull request body, a review comment -- inside objects with no
    oversized array anywhere. Those payloads compressed by 0%, and several by
    less than 0%.
    """
    import json

    from memor.compress.json_crush import compress_json

    payload = json.dumps({
        "title": "Fix the retry loop",
        "state": "open",
        "body": "context. " * 400,
    })
    out = compress_json(payload)
    assert len(out) < len(payload) / 2
    # Both ends of the value survive, and the elision is stated.
    assert "context." in out
    assert "memor:" in out
    assert "chars omitted" in out
    # Keys and short values are untouched: they are how the agent navigates.
    assert '"title"' in out and "Fix the retry loop" in out
    assert '"state"' in out and "open" in out


def test_diffs_and_tracebacks_are_never_truncated():
    """Some long values are the payload, not padding around it."""
    import json

    from memor.compress.json_crush import compress_json

    diff = "@@ -1,4 +1,4 @@\n" + "\n".join(f"-old line {i}" for i in range(80))
    trace = "Traceback (most recent call last):\n" + "\n".join(
        f'  File "x.py", line {i}' for i in range(80))
    payload = json.dumps({"diff_hunk": diff, "traceback": trace,
                          "body": "filler. " * 300})
    # Compare parsed values: the raw strings are JSON-escaped in the output.
    parsed = json.loads(compress_json(payload))
    assert parsed["diff_hunk"] == diff, "a diff must survive verbatim"
    assert parsed["traceback"] == trace, "a traceback must survive verbatim"
    assert "chars omitted" in parsed["body"], "the filler should still be cut"


def test_non_ascii_payloads_are_not_inflated():
    """ensure_ascii turned every accented character into a six-byte escape.

    On a real payload with fourteen non-ASCII characters and no compressible
    array, the "compressed" output was 42 tokens larger than the input.
    """
    import json

    from memor.compress.json_crush import compress_json

    payload = json.dumps({"note": "café naïve résumé — déjà vu", "n": 1},
                         ensure_ascii=False)
    out = compress_json(payload)
    assert len(out) <= len(payload)
    assert "café" in out


def test_output_is_never_longer_than_the_input():
    """A compressor that grows its input is worse than one that declines."""
    import json

    from memor.compress.json_crush import compress_json

    for payload in ('{"a":1}',
                    json.dumps({"k": "v"}),
                    json.dumps({"x": ["é"] * 5}, ensure_ascii=False)):
        assert len(compress_json(payload)) <= len(payload), payload


def test_truncated_values_keep_the_payload_parseable():
    import json

    from memor.compress.json_crush import compress_json

    payload = json.dumps({"body": "y " * 500, "items": list(range(50))})
    out = compress_json(payload)
    parsed = json.loads(out)          # must not raise
    assert "body" in parsed and "items" in parsed


def test_page_content_is_not_elided_to_a_stub():
    """`content` is the payload for a page scrape, not padding around it.

    Real regression: jcode's `browser` tool returns
    ``{"content": {"content": "<page text>", "url": ...}}``. The long-value
    rule truncated the page text to a 280-character stub, reporting a 98%
    "saving" while deleting the entire thing the agent navigated in order to
    read. A saving that removes the answer is a wrong answer, not a saving.
    """
    import json

    from memor.compress.json_crush import compress_json

    page = "Prices start at 29 EUR per seat. " * 400
    payload = json.dumps({"content": {"content": page, "url": "http://x/"}})
    parsed = json.loads(compress_json(payload))
    kept = parsed["content"]["content"]
    assert "29 EUR per seat" in kept
    assert len(kept) > len(page) * 0.5, "page content was gutted"
