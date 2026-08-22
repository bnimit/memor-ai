"""Compress unified diffs by eliding unchanged context, never the change.

A diff is the one code-adjacent payload with a safe compression story. Its
lines have declared roles:

  ``+``/``-``  the change itself. Byte-exact, always. This is the answer.
  ``@@``       where the change sits. Kept, or the model cannot locate it.
  `` ``        unchanged context, which is a copy of what is already on disk.

Only the last is removed, and only the middle of a long run: the lines
immediately around a change are what make it readable. Everything removed is
recoverable by reading the file, which is a stronger safety argument than the
log crusher can make -- it deletes lines that exist nowhere else.

Measured on 59 real diff payloads from local sessions: 58.2% of tokens are
``+``/``-`` lines that must survive, and 26.3% is context. That suggested a
ceiling near a quarter.

The realised figure is far smaller, and the reason is worth stating so nobody
re-derives the optimistic number. ``git diff`` emits three lines of context per
side by default, so on the real corpus context runs cluster at 3 and 10 lines:
499 runs are at or below the elision threshold and only 4 exceed it. Total
saving on 64 payloads is 385 tokens, 0.3%. The 26.3% of tokens that *are*
context is spread across many short runs, and short runs cannot be elided
without costing more in markers than they save.

This module therefore earns its place through the classifier change beside it,
not through its own savings: routing diffs away from ``looks_like_source``
means ``git log -p`` and review output are no longer treated as untouchable
source. It becomes worthwhile on its own terms only against diffs generated
with a larger ``-U`` context setting.

The hazard these payloads actually carry is not diff syntax at all. Agents run
``gh pr view``, ``git log -p`` and review scripts, so the same payload holds
``## Commits``, ``Author:``, commit subjects and prose wrapped around the diff.
A parser that assumes every non-``+``/``-`` line is droppable context deletes
the commit messages. So context is only ever elided *inside* a hunk -- after a
``@@`` marker and before the next non-diff line -- and anything outside a hunk
is passed through untouched.
"""
from __future__ import annotations

import re

#: Start of a hunk. Everything after it, until the run breaks, is diff body.
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")

#: File headers that bracket a hunk. Kept: they name what changed.
_FILE_HEADER = re.compile(r"^(diff --git |index |--- |\+\+\+ |new file mode |"
                          r"deleted file mode |similarity index |rename from |"
                          r"rename to |old mode |new mode |Binary files )")

#: Context lines kept on each side of a change inside a hunk.
_CONTEXT_KEPT = 3

#: A run of context shorter than this is left alone: eliding two lines to add a
#: one-line marker saves nothing and costs readability.
_MIN_ELIDE = 6

#: Below this many lines a diff is not worth touching.
MIN_LINES = 12


def looks_like_diff(text: str) -> bool:
    """True when the payload contains real unified-diff hunks.

    Requires an actual ``@@`` hunk header rather than a stray ``+``/``-``
    line, because prose and code both start lines with those.
    """
    if not text:
        return False
    hunks = 0
    for line in text.split("\n"):
        if _HUNK.match(line):
            hunks += 1
            if hunks >= 1:
                return True
    return False


def _elide_context_runs(body: list[str]) -> list[str]:
    """Within one hunk, shorten long runs of unchanged lines."""
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if len(run) <= _CONTEXT_KEPT * 2 + _MIN_ELIDE:
            out.extend(run)
        else:
            head = run[:_CONTEXT_KEPT]
            tail = run[-_CONTEXT_KEPT:]
            dropped = len(run) - len(head) - len(tail)
            out.extend(head)
            out.append(f"... [memor: omitted {dropped} unchanged lines] ...")
            out.extend(tail)
        run.clear()

    for line in body:
        # A context line starts with a space, or is empty (some tools strip the
        # trailing space on a blank context line).
        if line.startswith(" ") or line == "":
            run.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return out


def compress_diff(text: str) -> str:
    """Elide unchanged context inside hunks. Returns input when not worth it.

    Anything outside a hunk -- commit metadata, review prose, file lists -- is
    passed through verbatim.
    """
    if not looks_like_diff(text):
        return text
    lines = text.split("\n")
    if len(lines) < MIN_LINES:
        return text

    out: list[str] = []
    hunk_body: list[str] = []
    in_hunk = False

    def close_hunk() -> None:
        nonlocal in_hunk
        if in_hunk:
            out.extend(_elide_context_runs(hunk_body))
            hunk_body.clear()
            in_hunk = False

    for line in lines:
        if _HUNK.match(line):
            close_hunk()
            out.append(line)
            in_hunk = True
            continue
        if not in_hunk:
            out.append(line)
            continue
        # Inside a hunk. A file header means the next file's section began.
        if _FILE_HEADER.match(line):
            close_hunk()
            out.append(line)
            continue
        # `\ No newline at end of file` and +/-/context all belong to the body.
        if line[:1] in ("+", "-", " ", "\\") or line == "":
            hunk_body.append(line)
            continue
        # Anything else is not diff syntax: prose or a report resuming. End the
        # hunk rather than guess, so non-diff text is never treated as context.
        close_hunk()
        out.append(line)

    close_hunk()
    result = "\n".join(out)
    return result if len(result) < len(text) else text
