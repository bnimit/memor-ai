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
