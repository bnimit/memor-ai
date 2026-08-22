"""Tests for the diff compressor. Written before the implementation.

The cases come from replaying 59 real diff payloads out of local sessions, so
the hazards here are ones that actually occur rather than ones imagined.
"""
from __future__ import annotations


def test_changed_lines_survive_byte_exact():
    """The +/- lines ARE the change. Losing one is losing the answer."""
    from memor.compress.diff import compress_diff

    added = [f"+    self.field_{i} = compute_{i}()" for i in range(40)]
    removed = [f"-    legacy_{i} = None" for i in range(10)]
    lines = ["diff --git a/mod.py b/mod.py", "--- a/mod.py", "+++ b/mod.py",
             "@@ -1,60 +1,90 @@"]
    lines += [f"     context line {i}" for i in range(30)]
    lines += added + removed
    lines += [f"     trailing context {i}" for i in range(30)]
    out = compress_diff("\n".join(lines))

    for line in added + removed:
        assert line in out, f"lost a changed line: {line!r}"


def test_hunk_headers_survive():
    """`@@ -a,b +c,d @@` is how the model locates the change in the file."""
    from memor.compress.diff import compress_diff

    lines = ["diff --git a/x.py b/x.py"]
    for h in range(4):
        lines.append(f"@@ -{h*100},20 +{h*100},25 @@ def handler_{h}():")
        lines += [f"     ctx {h}.{i}" for i in range(20)]
        lines.append(f"+    added in hunk {h}")
    out = compress_diff("\n".join(lines))

    for h in range(4):
        assert f"@@ -{h*100},20 +{h*100},25 @@" in out, f"lost hunk header {h}"


def test_context_is_elided_with_a_stated_count():
    """Unchanged context is recoverable from disk, so it may go -- but the
    reader must be able to tell that something was removed."""
    from memor.compress.diff import compress_diff

    lines = ["diff --git a/big.py b/big.py", "@@ -1,200 +1,201 @@"]
    lines += [f"     unchanged {i}" for i in range(200)]
    lines.append("+    one new line")
    text = "\n".join(lines)
    out = compress_diff(text)

    assert len(out) < len(text)
    assert "+    one new line" in out
    assert "memor:" in out and "omitted" in out


def test_git_log_metadata_is_never_treated_as_context():
    """Real regression source: these payloads carry `## Commits`, `Author:`,
    commit subjects and review prose alongside the diff. A parser that treats
    every non-+/- line as droppable context deletes the commit messages."""
    from memor.compress.diff import compress_diff

    lines = [
        "# Review package: 663b7f62774f6d3c8e4d9a685ea1ee1d21dfe601..HEAD",
        "## Commits",
        "050f2b6e feat(sdk): offline invoice payment methods",
        "9a1b2c3d fix(api): reject negative amounts",
        "## Files changed",
        "apps/backend/backend/api/v1/evm_pay.py",
        "## Diff",
        "diff --git a/apps/backend/backend/api/v1/evm_pay.py b/apps/backend/backend/api/v1/evm_pay.py",
        "@@ -10,30 +10,31 @@",
    ]
    lines += [f"     unchanged {i}" for i in range(30)]
    lines.append("+    validated = True")
    out = compress_diff("\n".join(lines))

    assert "050f2b6e feat(sdk): offline invoice payment methods" in out
    assert "9a1b2c3d fix(api): reject negative amounts" in out
    assert "## Commits" in out
    assert "+    validated = True" in out


def test_declines_when_there_is_nothing_to_gain():
    """A diff that is nearly all changes has no context to remove."""
    from memor.compress.diff import compress_diff

    lines = ["diff --git a/n.py b/n.py", "@@ -0,0 +1,50 @@"]
    lines += [f"+line {i}" for i in range(50)]
    text = "\n".join(lines)

    assert compress_diff(text) == text


def test_not_a_diff_is_returned_untouched():
    from memor.compress.diff import compress_diff

    for text in ("", "just some prose\nwith lines\n", "a,b,c\n1,2,3\n"):
        assert compress_diff(text) == text


def test_detects_diff_before_the_source_guard_claims_it():
    """git output contains code, so `looks_like_source` claims it. Diff has to
    be checked first or the compressor can never run."""
    from memor.compress import detect_content_type

    lines = ["diff --git a/svc.py b/svc.py", "--- a/svc.py", "+++ b/svc.py",
             "@@ -1,40 +1,42 @@"]
    lines += [f"     def helper_{i}(self):" for i in range(20)]
    lines += [f"+    def added_{i}(self):" for i in range(6)]
    lines += [f"-    def gone_{i}(self):" for i in range(2)]

    assert detect_content_type("\n".join(lines)) == "diff"


def test_end_to_end_through_compress_text():
    """The public entry point must route and compress a diff."""
    from memor.compress import compress_text

    lines = ["diff --git a/a.py b/a.py", "@@ -1,120 +1,121 @@"]
    lines += [f"     stable line {i}" for i in range(120)]
    lines.append("+    the one change")
    r = compress_text("\n".join(lines))

    assert r.content_type == "diff"
    assert r.passthrough is False
    assert r.tokens_after < r.tokens_before
    assert "+    the one change" in r.text


def test_hook_path_compresses_diffs_too():
    """The hook gates on `looks_like_source` before `compress_text`.

    A diff body is full of code, so the guard claims it, and the diff
    compressor never runs on the path that does the real work. Same defect
    class as the fetched-document release: routing a content type correctly in
    `detect_content_type` is not enough when a caller has its own gate.
    """
    from memor.posttool_compress import build_response

    lines = ["diff --git a/x.py b/x.py", "@@ -1,120 +1,121 @@"]
    lines += [f"     stable_{i} = compute()" for i in range(120)]
    lines.append("+    added = True")
    out = build_response({
        "tool_name": "bash",
        "tool_response": {"stdout": "\n".join(lines), "exit_code": 0},
    })
    assert out, "hook declined a diff"
    text = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    assert "+    added = True" in text
    assert "@@ -1,120 +1,121 @@" in text


def test_hook_still_refuses_a_plain_source_dump():
    """The guard must keep protecting a bare file dump on the hook path."""
    from memor.posttool_compress import build_response

    src = "\n".join([
        "import json",
        "from typing import Any",
        "",
        "class Handler:",
        "    def __init__(self, config: dict[str, Any]) -> None:",
        "        self.config = config",
        "    def process(self, payload: str) -> dict:",
        "        return json.loads(payload)",
    ] * 20)
    out = build_response({
        "tool_name": "bash",
        "tool_response": {"stdout": src, "exit_code": 0},
    })
    assert out == {}, "hook rewrote a plain source dump"
