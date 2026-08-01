from memor.compress import compress_text

def test_log_keeps_error_drops_info_noise():
    lines = [f"2026-08-01 INFO fine {i}" for i in range(100)]
    lines.append("ERROR something failed")
    lines.append("Traceback (most recent call last):")
    lines.append('  File "app.py", line 1')
    text = "\n".join(lines)
    r = compress_text(text, content_type="log")
    assert r.passthrough is False
    assert "ERROR something failed" in r.text
    assert "Traceback" in r.text
    assert r.tokens_after < r.tokens_before
