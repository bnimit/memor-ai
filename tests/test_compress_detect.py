from memor.compress.detect import detect_content_type

def test_detect_json():
    assert detect_content_type('[{"a":1},{"a":2}]') == "json"

def test_detect_log():
    sample = "[2026-08-01 10:00:00] INFO ok\n" * 20 + "ERROR boom\nTraceback (most recent call last):\n  File x\n"
    assert detect_content_type(sample) == "log"

def test_detect_search():
    assert detect_content_type("path/to/file.py:12: matched line\nother.py:3: hit\nthird.py:5: another") == "search"

def test_clock_timestamps_do_not_read_as_search():
    sample = "\n".join(f"[2026-08-01 10:00:0{i}] request handled" for i in range(6))
    assert detect_content_type(sample) == "log"

def test_bare_timestamp_prefix_is_not_search():
    sample = "\n".join(f"10:00:0{i} started worker {i}" for i in range(6))
    assert detect_content_type(sample) == "log"


def test_pytest_progress_output_is_detected_as_log():
    """pytest's default progress format is the most common noisy tool output.

    Without this it scored zero test-result markers and fell through to
    "text", so a 400-line run reached the model whole. Measured on a real run
    of this repo's own suite: 1,975 -> 222 tokens once detected.
    """
    from memor.compress.detect import detect_content_type

    out = "\n".join([
        "============================= test session starts ==============================",
        "tests/test_alpha.py ........                                             [  8%]",
        "tests/test_beta.py .....s..                                              [ 20%]",
        "tests/test_gamma.py ..........                                           [ 35%]",
        "tests/test_delta.py ..F...                                               [ 47%]",
        "======================== 1188 passed, 1 warning in 6.02s =======================",
    ])
    assert detect_content_type(out) == "log"


def test_pytest_progress_pattern_does_not_fire_on_prose():
    """The percentage suffix is what makes the pattern safe.

    Ordinary prose mentioning a percentage must not be classified as test
    output and handed to a crusher that deletes "repetitive" lines.
    """
    from memor.compress.detect import detect_content_type

    prose = "\n".join([
        "The migration completed and coverage rose to 84%.",
        "We reviewed the report [see appendix] and agreed on the plan.",
        "Latency improved by 12% after the cache change.",
        "Nothing here is test-runner output at all.",
    ])
    assert detect_content_type(prose) != "log"


def test_source_code_still_wins_over_test_progress():
    from memor.compress.detect import detect_content_type

    src = "\n".join(
        f"def check_{i}(value):\n    return transform(value, {i});" for i in range(20)
    )
    assert detect_content_type(src) == "source"
