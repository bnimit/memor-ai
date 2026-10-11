from __future__ import annotations
import re

# Python and JVM/JS stack traces. A traceback is one unit: its last line is the
# exception, and a keyword window around the header cannot know how far down
# that line is. `httpx.ReadTimeout: timed out` names no error vocabulary at all,
# so a ±2 window kept the frames and deleted the one line saying what happened.
_PY_TRACE_HEAD = "Traceback (most recent call last)"
_PY_TRACE_CHAIN = (
    "During handling of the above exception",
    "The above exception was the direct cause",
)
_JVM_FRAME = re.compile(
    r"^\s+at \S"                                  # Java / JS frame
    r"|^\s*Caused by: "
    r"|^\s*Suppressed: "
    r"|^\s+\.\.\. \d+ (?:more|common frames omitted)"
)

#: A traceback longer than this keeps its head and its tail; the deepest frames
#: and the exception are at the bottom, the entry point at the top.
_TRACE_MAX_LINES = 50
_TRACE_HEAD_KEEP = 10
_TRACE_TAIL_KEEP = 25

# WARN is a log level, matched as one: the uppercase token, `level=warn`, or
# nginx's `[warn]`. Lowercase "warning" in prose is not, and the source guard
# upstream already keeps `var(--warn)` stylesheets away.
_WARN = re.compile(r"\bWARN(?:ING)?\b|(?i:\blevel=[\"']?warn(?:ing)?\b|\[warn(?:ing)?\])")

# A server-error response in an access log. It is the request-side view of an
# outage (which endpoints failed, and when) and names no error vocabulary:
# `status=503` or nginx's `"GET /x HTTP/1.1" 503 1234`.
_HTTP_5XX = re.compile(r'\bstatus(?:_code)?[=:]\s*"?5\d\d\b|" 5\d\d \d')

# Repeats are collapsed only in structured log records: a line carrying a clock
# time or a level token. Test-runner output has neither, and there two lines
# that differ only in `test_1` versus `test_2` are two different failures.
_STRUCTURED = re.compile(
    r"\d{2}:\d{2}:\d{2}|\b(?:INFO|DEBUG|WARN(?:ING)?|ERROR|FATAL|CRITICAL|TRACE)\b"
)
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX_ID = re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}\b")
# A number that stands alone. Digits glued to a word (`test_1`, `v2`, `u_42`)
# are part of a name and are left in the template.
_NUMBER = re.compile(r"(?<![A-Za-z_])\d+(?:\.\d+)?")

#: A record seen this many times or more keeps its first and last occurrence.
_REPEAT_MIN = 3


def _template(line: str) -> str:
    """The line with request ids, hashes, numbers and timestamps blanked out."""
    line = _UUID.sub("#", line)
    line = _HEX_ID.sub("#", line)
    return _NUMBER.sub("#", line)


def _traceback_blocks(lines: list[str], skip: set[int]) -> list[tuple[int, int]]:
    """(start, end) inclusive spans of every stack trace in ``lines``."""
    blocks: list[tuple[int, int]] = []
    n = len(lines)
    i = 0
    while i < n:
        if i in skip:
            i += 1
            continue
        line = lines[i]
        if _PY_TRACE_HEAD in line:
            start = i
            end = i
            while True:
                j = end + 1
                while j < n and lines[j][:1] in (" ", "\t") and lines[j].strip():
                    j += 1
                # The first unindented line after the frames is the exception.
                end = j if j < n and lines[j].strip() else j - 1
                # A chained exception continues the same trace.
                k = end + 1
                while k < n and not lines[k].strip():
                    k += 1
                if k < n and lines[k].lstrip().startswith(_PY_TRACE_CHAIN):
                    t = k + 1
                    while t < n and not lines[t].strip():
                        t += 1
                    if t < n and _PY_TRACE_HEAD in lines[t]:
                        end = t
                        continue
                break
            blocks.append((start, end))
            i = end + 1
            continue
        if i + 1 < n and _JVM_FRAME.match(lines[i + 1]) and not _JVM_FRAME.match(line):
            end = i + 1
            while end + 1 < n and _JVM_FRAME.match(lines[end + 1]):
                end += 1
            blocks.append((i, end))
            i = end + 1
            continue
        i += 1
    return blocks


def compress_log(text: str) -> str:
    """Compress log content by keeping important lines and context."""
    lines = text.split('\n')
    
    if len(lines) <= 15:
        return text
    
    # Pattern for important log lines
    important_pattern = re.compile(
        r'(?i)(error|fatal|traceback|exception|failed|CRITICAL)',
        re.IGNORECASE
    )

    # A run's verdict is the one line the agent is actually reading for, and it
    # is not always inside the last five lines: vitest prints `Test Files 5
    # passed` above a duration line, trailing blanks and a wrapper footer, so a
    # positional rule alone dropped it 20 lines from the end. Keeping it is
    # worth more than the handful of tokens it costs, and unlike the error
    # pattern it must match *successful* runs too.
    summary_pattern = re.compile(
        r'(?i)^\s*(?:test files|tests|suites?)\s+\d+\s+(?:passed|failed)'
        r'|\b\d+\s+(?:passed|failed|skipped|error|errors|warnings?)\b'
        r'|^\s*(?:ok|FAIL)\s+\S+\s+[\d.]+m?s'
        r'|\b(?:test|build|suite)s?\s+(?:passed|failed|succeeded|complete)\b'
    )

    # A ``` fence is an explicit "this is verbatim" marker. Background-task
    # reports and design notes embed snippets and test output in fences, and
    # the repetition rules below cannot tell one from a log line: a retention
    # probe over real sessions found 345 fenced lines being elided. Everything
    # between a pair of fences is kept.
    fenced: set[int] = set()
    inside = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced.add(i)
            inside = not inside
            continue
        if inside:
            fenced.add(i)

    important_indices = set(fenced)
    # Notes printed after a kept line, saying what was collapsed into it.
    notes: dict[int, str] = {}

    # Stack traces are kept whole, once. An identical trace (ids and numbers
    # aside) adds nothing the first copy did not say, so later copies go; the
    # ERROR record that announced each one is collapsed by the repeat rule.
    in_trace: set[int] = set()
    repeat_trace: set[int] = set()
    trace_groups: dict[tuple[str, ...], list[tuple[int, int]]] = {}
    for start, end in _traceback_blocks(lines, fenced):
        in_trace.update(range(start, end + 1))
        key = tuple(_template(ln) for ln in lines[start:end + 1])
        trace_groups.setdefault(key, []).append((start, end))
    for spans in trace_groups.values():
        for rs, re_ in spans[1:]:
            repeat_trace.update(range(rs, re_ + 1))
        start, end = spans[0]
        # The two lines either side, as the keyword window always gave it.
        important_indices.update(
            i for i in range(start - 2, end + 3) if 0 <= i < len(lines) and not start <= i <= end
        )
        if end - start + 1 > _TRACE_MAX_LINES:
            important_indices.update(range(start, start + _TRACE_HEAD_KEEP))
            important_indices.update(range(end - _TRACE_TAIL_KEEP + 1, end + 1))
        else:
            important_indices.update(range(start, end + 1))
        if len(spans) > 1:
            notes[end] = (f"... [memor: this traceback occurs {len(spans)} times; "
                          f"the {len(spans) - 1} identical repeats are omitted] ...")

    # Anchors: error-ish and verdict lines take ±2 lines of context, WARN lines
    # are kept on their own. A warning is usually self-contained, and it is
    # often the only early sign of what later fails.
    anchors: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        if i in fenced or i in in_trace:
            continue
        if important_pattern.search(line) or summary_pattern.search(line):
            anchors.append((i, 2))
        elif _WARN.search(line) or _HTTP_5XX.search(line):
            anchors.append((i, 0))

    # A record repeated many times is kept twice, first and last, so the reader
    # still sees when it started and when it stopped. Fifty identical pool
    # timeouts are one fact.
    groups: dict[str, list[int]] = {}
    for i, _ in anchors:
        if _STRUCTURED.search(lines[i]):
            groups.setdefault(_template(lines[i]), []).append(i)
    suppressed: set[int] = set()
    for members in groups.values():
        if len(members) >= _REPEAT_MIN:
            suppressed.update(members[1:-1])
            notes[members[0]] = (
                f"... [memor: the line above occurs {len(members)} times "
                f"(ids and numbers aside); the last is kept below] ..."
            )

    for i, radius in anchors:
        if i in suppressed:
            continue
        for idx in range(i - radius, i + radius + 1):
            # Context never reaches into a repeated trace: a fragment of one
            # would read as a different, truncated error.
            if 0 <= idx < len(lines) and idx not in repeat_trace:
                important_indices.add(idx)
    
    # Always keep first 5 and last 5 lines
    for i in range(min(5, len(lines))):
        important_indices.add(i)
    for i in range(max(0, len(lines) - 5), len(lines)):
        important_indices.add(i)
    
    # Build result
    if not important_indices:
        # No important lines found, keep first/last and sample middle
        result_lines = lines[:5]
        if len(lines) > 10:
            dropped = len(lines) - 10
            result_lines.append(f"... [memor: omitted {dropped} lines] ...")
            result_lines.extend(lines[-5:])
        else:
            result_lines.extend(lines[5:])
        return '\n'.join(result_lines)
    
    # Sort indices to maintain order
    sorted_indices = sorted(important_indices)
    
    result_lines = []
    prev_idx = -2
    
    for idx in sorted_indices:
        if idx > prev_idx + 1:
            # Gap: state how many lines went missing so the reader can tell
            # truncated output from complete output.
            gap = idx if prev_idx < 0 else idx - prev_idx - 1
            if gap > 0:
                result_lines.append(f"... [memor: omitted {gap} lines] ...")
        result_lines.append(lines[idx])
        if idx in notes:
            result_lines.append(notes[idx])
        prev_idx = idx
    
    return '\n'.join(result_lines)
