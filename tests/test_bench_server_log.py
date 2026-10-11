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


def test_every_distinct_signal_survives():
    """Repeats may collapse, but no distinct error, warning, frame or 5xx may go.

    The INFO-only needles (the config change that caused the incident, the
    rollback) are deliberately not asserted: a keyword crusher cannot know an
    innocuous INFO line matters, and the benchmark reports that as LOST.
    """
    b = build_log(lines=2000)
    r = compress_text("\n".join(b.lines))
    ret = retention(b, r.text)
    for k in ("error_lines", "warn_lines", "traceback_frames", "status_5xx_lines",
              "incident_onset", "shutdown_summary"):
        _, _, kept, total = ret[k]
        assert total > 0 and kept == total, f"{k}: {kept}/{total} distinct kept"
    assert r.tokens_after < r.tokens_before * 0.05
