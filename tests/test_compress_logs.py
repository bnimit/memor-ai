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


def test_fenced_code_inside_a_log_is_not_crushed():
    """A log that quotes code in a fence must keep the fenced lines.

    Found by a retention probe over real sessions: 345 fenced lines were being
    elided across log-classified payloads. Background-task reports and design
    notes embed test output and snippets in ``` fences, and the crusher's
    repetition rules do not know a fence from a log line. The fence is an
    explicit "this is verbatim" marker and has to be honoured.
    """
    lines = [f"2026-01-01 10:00:00 INFO step {i}" for i in range(80)]
    lines += [
        "```python",
        "amount_remaining = invoice_total",
        "for payment in payments:",
        "    amount_remaining -= payment.amount",
        "```",
    ]
    lines += [f"2026-01-01 10:00:00 INFO step {i}" for i in range(80, 160)]
    text = "\n".join(lines)

    r = compress_text(text, content_type="log")
    for line in ("amount_remaining = invoice_total",
                 "for payment in payments:",
                 "    amount_remaining -= payment.amount"):
        assert line in r.text, f"crusher ate fenced code: {line!r}"
    assert r.tokens_after < r.tokens_before


def _noise(n, start=0):
    return [f"2026-10-11T14:00:{(start + i) % 60:02d}.000Z INFO  http req_id={i:012x} "
            f"method=GET path=/api/v1/orders/{i} status=200 dur_ms=12" for i in range(n)]


def test_traceback_keeps_exception_line_without_error_vocabulary():
    """The exception is the last line of a traceback, however long it is.

    Real gap, found by scripts/bench_server_log.py: a ±2 window around the
    `Traceback` header kept the first frames and dropped
    `httpx.ReadTimeout: timed out`, which names no error keyword, so every
    copy of the one line saying what happened was deleted.
    """
    trace = [
        "Traceback (most recent call last):",
        '  File "/app/orders/payments/capture.py", line 94, in capture',
        "    resp = await self._client.post(url, json=body, timeout=8.0)",
        '  File "/usr/local/lib/python3.12/site-packages/httpx/_client.py", line 1905, in post',
        "    return await self.request(",
        "httpx.ReadTimeout: timed out after 8.0s waiting for payments-gw.internal:443",
    ]
    text = "\n".join(_noise(60) + trace + _noise(60, 60))
    r = compress_text(text, content_type="log")
    for line in trace:
        assert line in r.text, f"traceback line lost: {line!r}"
    assert r.tokens_after < r.tokens_before / 3


def test_chained_python_traceback_is_one_unit():
    trace = [
        "Traceback (most recent call last):",
        '  File "a.py", line 1, in f',
        "    g()",
        "KeyboardInterrupt",
        "",
        "During handling of the above exception, another exception occurred:",
        "",
        "Traceback (most recent call last):",
        '  File "a.py", line 9, in main',
        "    cleanup()",
        "SystemExit: 3",
    ]
    text = "\n".join(_noise(40) + trace + _noise(40, 40))
    r = compress_text(text, content_type="log")
    for line in trace:
        if line:
            assert line in r.text, f"chained trace line lost: {line!r}"


def test_java_stack_trace_keeps_frames_and_cause():
    trace = [
        "2026-10-11 14:00:01,123 ERROR [main] OrderService - request failed",
        "java.lang.IllegalStateException: pool closed",
        "\tat com.acme.orders.Pool.acquire(Pool.java:88)",
        "\tat com.acme.orders.OrderService.get(OrderService.java:41)",
        "\tat com.acme.http.Router.dispatch(Router.java:120)",
        "\tat com.acme.http.Router.handle(Router.java:97)",
        "Caused by: java.net.SocketTimeoutException: connect timed out",
        "\tat java.base/java.net.Socket.connect(Socket.java:633)",
        "\t... 12 more",
    ]
    text = "\n".join(_noise(40) + trace + _noise(40, 40))
    r = compress_text(text, content_type="log")
    for line in trace:
        assert line in r.text, f"java trace line lost: {line!r}"


def test_identical_tracebacks_are_kept_once():
    trace = [
        "Traceback (most recent call last):",
        '  File "/app/db.py", line 58, in session',
        "    conn = await pool.acquire()",
        "sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 reached",
    ]
    lines = []
    for k in range(6):
        lines += _noise(30, k * 30)
        lines.append(f"2026-10-11T14:01:{k:02d}.000Z ERROR db req_id={k:012x} could not acquire connection")
        lines += trace
    lines += _noise(30)
    r = compress_text("\n".join(lines), content_type="log")
    assert r.text.count("sqlalchemy.exc.TimeoutError") == 1
    assert "this traceback occurs 6 times" in r.text


def test_repeated_records_keep_first_last_and_count():
    """Fifty identical pool timeouts are one fact, with a start and an end."""
    lines = []
    for k in range(50):
        lines += _noise(8, k)
        lines.append(f"2026-10-11T14:{k:02d}:00.000Z ERROR db req_id={k + 1000:012x} "
                     f"could not acquire connection, waited {k * 10}ms")
    lines.append("2026-10-11T14:59:00.000Z ERROR payments capture failed for order 7")
    lines += _noise(8)
    r = compress_text("\n".join(lines), content_type="log")
    kept = [ln for ln in r.text.split("\n") if "could not acquire connection" in ln]
    assert len(kept) == 2
    assert "14:00:00.000Z" in kept[0] and "14:49:00.000Z" in kept[1]
    assert "occurs 50 times" in r.text
    assert "capture failed for order 7" in r.text


def test_test_runner_failures_are_never_collapsed():
    """`FAILED test_1` and `FAILED test_2` are two failures, not a repeat."""
    lines = [f"tests/test_mod.py::test_case_{i} PASSED" for i in range(60)]
    lines += [f"FAILED tests/test_mod.py::test_case_{i} - AssertionError" for i in range(10)]
    lines += ["===== 10 failed, 60 passed in 1.20s ====="]
    r = compress_text("\n".join(lines), content_type="log")
    for i in range(10):
        assert f"FAILED tests/test_mod.py::test_case_{i} - AssertionError" in r.text


def test_warn_lines_survive_without_error_vocabulary():
    """A warning is often the only early sign of what later fails."""
    warns = [
        "2026-10-11T14:13:48.498Z WARN  db pool acquire slow wait_ms=1689 pool.waiting=4",
        '2026/10/11 14:13:50 [warn] 31#31: *812 upstream response is buffered to a temporary file',
        'ts=2026-10-11T14:13:52Z level=warn msg="disk usage above 85%"',
    ]
    lines = _noise(40) + [warns[0]] + _noise(40, 40) + [warns[1]] + _noise(40, 20) + [warns[2]] + _noise(40, 30)
    r = compress_text("\n".join(lines), content_type="log")
    for w in warns:
        assert w in r.text, f"warning lost: {w!r}"


def test_server_error_responses_survive():
    """A 503 in an access log names no error keyword, but it is the outage."""
    hits = [
        "2026-10-11T14:14:31.300Z INFO http req_id=4d3a method=POST path=/api/v1/orders status=503 dur_ms=44",
        '10.0.0.7 - - [11/Oct/2026:14:14:32 +0000] "GET /api/v1/orders/9 HTTP/1.1" 502 157 "-" "curl/8.5"',
    ]
    lines = _noise(50) + [hits[0]] + _noise(50, 50) + [hits[1]] + _noise(50, 40)
    r = compress_text("\n".join(lines), content_type="log")
    for h in hits:
        assert h in r.text
