from memor.compress import compress_text

def test_json_array_keeps_structure_shrinks():
    arr = [{"id": i, "ok": True, "blob": "x" * 50} for i in range(40)]
    import json
    text = json.dumps(arr)
    r = compress_text(text, content_type="json")
    assert r.tokens_after < r.tokens_before
    assert r.passthrough is False
