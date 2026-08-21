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


def test_manifest_with_incidental_dates_is_not_gutted():
    """A file manifest must survive a few date-like lines.

    Real regression: `gh pr view --files` output classified as ``log`` because
    3 of 84 lines carried an ISO date, after which the log crusher kept only
    the first and last five lines and deleted 66 filenames. For a manifest the
    list *is* the answer, so this is a silent wrong answer rather than a
    saving.
    """
    lines = ["Some PR title | MERGED 2026-08-20T06:48:16Z", "--- files ---"]
    lines += [f"apps/backend/backend/mod_{i}.py" for i in range(70)]
    text = "\n".join(lines)

    r = compress_text(text)
    survived = sum(1 for i in range(70) if f"mod_{i}.py" in r.text)
    assert survived >= 63, f"manifest gutted: only {survived}/70 paths survived"


def test_git_log_stat_keeps_changed_files():
    """`git log --stat` is a manifest with timestamps, not a log to crush."""
    lines = []
    for c in range(3):
        lines.append(f"53413c{c} 2026-08-05 10:51:59 +0530 Some commit subject")
        lines += [f" memor/mod_{c}_{i}.py | {i} +++" for i in range(15)]
        lines.append(" 15 files changed, 240 insertions(+)")
    text = "\n".join(lines)

    r = compress_text(text)
    survived = sum(
        1 for c in range(3) for i in range(15) if f"mod_{c}_{i}.py" in r.text
    )
    assert survived >= 40, f"diffstat gutted: only {survived}/45 paths survived"


def test_run_summary_survives_even_when_not_in_last_lines():
    """The verdict line is what the agent is reading for.

    Real regression: vitest prints `Test Files 5 passed` above a duration line,
    blank lines and a wrapper footer, so the keep-last-five rule preserved only
    the footer and deleted both counts 20 lines from the end. A successful run
    matches none of the error keywords, so nothing else rescued it.
    """
    lines = [f"stderr | noisy line {i}" for i in range(60)]
    lines += [
        " Test Files  5 passed (5)",
        "      Tests  15 passed (15)",
        "   Start at  14:15:40",
        "   Duration  1.60s (transform 617ms, setup 590ms)",
        "",
        "",
        "--- Command finished with exit code: 0 ---",
    ]
    text = "\n".join(lines)

    r = compress_text(text, content_type="log")
    assert "Test Files  5 passed (5)" in r.text
    assert "Tests  15 passed (15)" in r.text
    assert r.tokens_after < r.tokens_before


def test_run_summary_survives_through_real_detection():
    """The committed verdict test pins ``content_type="log"``.

    That skips ``detect_content_type``, so it cannot catch a payload being
    routed to a different compressor than the one under test. An end-to-end
    check over the installed hook found exactly that: a prose-shaped vitest
    run classifies as ``text`` (lossless tidy, nothing removed) rather than
    ``log``, so the earlier test proved less than it appeared to.
    """
    lines = [f"2026-01-01 10:00:00 INFO vitest chatter line {i}" for i in range(120)]
    lines += [
        " Test Files  5 passed (5)",
        "      Tests  15 passed (15)",
        "   Duration  1.60s",
        "",
        "--- Command finished with exit code: 0 ---",
    ]
    text = "\n".join(lines)

    r = compress_text(text)          # no content_type: let detection decide
    assert r.content_type == "log"
    assert "Test Files  5 passed (5)" in r.text
    assert "Tests  15 passed (15)" in r.text
    assert r.tokens_after < r.tokens_before
