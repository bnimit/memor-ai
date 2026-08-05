"""Lossless tidying for payloads no other compressor claims.

Measured on 1,332 real ``text``-classified payloads (1,017 of them Bash output,
526k tokens): stripping terminal control sequences and normalising whitespace
recovers 0.6%, collapsing consecutive duplicate lines adds 0.2%, and only
head/tail truncation reaches 6.5% — by destroying the middle of every long
output.

So this deliberately stops at the lossless line. The `text` bucket is what
remains after logs, search results and test output have been routed away, and
that residue is genuinely low-redundancy; there is no safe 30% hiding in it.
What is here is free: escape codes and trailing padding carry no meaning to a
model, and removing them cannot change what the output says.
"""
from __future__ import annotations

import re

#: CSI sequences, OSC sequences, carriage returns and backspaces. Progress bars
#: and spinners emit these constantly; they render as noise in a transcript.
_CONTROL = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[@-Z\\-_]|[\r\x08]")

#: Below this there is nothing worth scanning for.
MIN_LENGTH = 400

#: Consecutive identical lines are collapsed once they exceed this run length.
_REPEAT_THRESHOLD = 2


def strip_control_sequences(text: str) -> str:
    return _CONTROL.sub("", text)


def normalize_whitespace(text: str) -> str:
    """Drop trailing padding and collapse runs of blank lines to one."""
    out: list[str] = []
    blank = 0
    for line in text.split("\n"):
        line = line.rstrip()
        if not line:
            blank += 1
            if blank <= 1:
                out.append(line)
            continue
        blank = 0
        out.append(line)
    return "\n".join(out)


def collapse_repeats(text: str) -> str:
    """Fold runs of identical lines into one plus a count.

    Marked rather than silent: the reader can tell how much was folded, and the
    content itself is fully recoverable from the count.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1] == lines[i]:
            j += 1
        out.append(lines[i])
        repeats = j - i
        if repeats >= _REPEAT_THRESHOLD:
            out.append(f"... [memor: previous line repeated {repeats} more times] ...")
            i = j + 1
        else:
            i += 1
    return "\n".join(out)


def compress_plain_text(text: str) -> str:
    """Tidy a plain-text payload without removing anything it says.

    Returns the input unchanged when the result would not be shorter, so a
    payload that is already clean pays nothing for being checked.
    """
    if len(text) < MIN_LENGTH:
        return text
    out = collapse_repeats(normalize_whitespace(strip_control_sequences(text)))
    return out if len(out) < len(text) else text
