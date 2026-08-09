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


def test_prose_quoting_a_few_pytest_lines_is_not_treated_as_test_output():
    """A count-based rule silently destroyed real documents.

    An implementation report in this repo quotes four pytest progress lines
    among 145 lines of prose. Under a bare count-of-three rule it was
    reclassified from prose to log and the crusher deleted 86 lines of
    explanation. Genuine test output is dominated by progress lines; a
    document that merely quotes some is not.
    """
    from memor.compress.detect import detect_content_type

    doc = "\n".join(
        ["# Implementation report", "", "## Summary", ""]
        + [f"This section explains decision {i} and why it was made that way."
           for i in range(60)]
        + ["", "Test output was:", "",
           "tests/test_alpha.py ....                                       [ 25%]",
           "tests/test_beta.py .....                                       [ 60%]",
           "tests/test_gamma.py ..                                         [100%]",
           "", "Everything passed, so the change was accepted."]
    )
    assert detect_content_type(doc) != "log"


def test_quiet_pytest_progress_without_filenames_is_detected():
    """`pytest -q` emits bare status runs with no filename prefix.

    Requiring a leading token matched only the verbose form and missed the
    shape that CI logs and -q runs actually produce.
    """
    from memor.compress.detect import detect_content_type

    out = "\n".join(["." * 72 + f" [{i * 5:3d}%]" for i in range(18)])
    assert detect_content_type(out) == "log"


def test_dense_test_output_still_detected_with_some_prose():
    from memor.compress.detect import detect_content_type

    out = "\n".join(
        ["============================= test session starts ============================="]
        + [f"tests/test_mod{i}.py ......                                    [{i * 5:3d}%]"
           for i in range(18)]
        + ["========================= 1188 passed in 6.02s ========================="]
    )
    assert detect_content_type(out) == "log"
