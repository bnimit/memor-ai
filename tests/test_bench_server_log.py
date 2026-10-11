"""The server-log benchmark must stay deterministic and exercise the real paths.

Its figures are only comparable across commits if the same seed yields the same
log, and only meaningful if the log reaches the log crusher rather than being
declined as prose or source.
"""
from scripts.bench_server_log import _hook_path, build_log, retention
from memor.compress import compress_text


def test_same_seed_same_log():
    a = build_log(lines=800, seed=7)
    b = build_log(lines=800, seed=7)
    assert a.lines == b.lines
    assert abs(len(a.lines) - 800) <= 10


def test_log_reaches_the_crusher_through_the_hook():
    b = build_log(lines=800)
    text = "\n".join(b.lines)
    r = compress_text(text)
    assert r.content_type == "log"
    assert r.tokens_after < r.tokens_before
    hooked, fired = _hook_path(text)
    assert fired and hooked == r.text
    kept, total = retention(b, r.text)["error_lines"]
    assert total > 0 and kept == total
