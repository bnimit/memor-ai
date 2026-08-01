from memor.compress.detect import detect_content_type

def test_detect_json():
    assert detect_content_type('[{"a":1},{"a":2}]') == "json"

def test_detect_log():
    sample = "2026-08-01 10:00:00 INFO ok\n" * 20 + "ERROR boom\nTraceback (most recent call last):\n  File x\n"
    assert detect_content_type(sample) == "log"

def test_detect_search():
    assert detect_content_type("path/to/file.py:12: matched line\nother.py:3: hit") == "search"
