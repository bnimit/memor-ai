"""Plain-text tidying: lossless only.

The `text` bucket is the residue after logs, search results and test output are
routed away. Measured on 1,332 real payloads, safe transformations recover 0.6%
and only truncation reaches 6.5%. So the contract here is narrow and strict:
remove things that carry no meaning, never remove anything the payload says.
"""
from __future__ import annotations

from memor.compress import compress_text
from memor.compress.text import (
    MIN_LENGTH,
    collapse_repeats,
    compress_plain_text,
    normalize_whitespace,
    strip_control_sequences,
)


def _long(body: str) -> str:
    """Pad past MIN_LENGTH so the compressor engages.

    Filler lines are distinct — identical padding would be folded by the
    repeat-collapser and make "unchanged" assertions fail for the wrong reason.
    """
    filler = "\n".join(f"filler line {i} with some content" for i in range(30))
    return body + "\n" + filler + "\n"


# --- what it removes ---------------------------------------------------------


def test_ansi_colour_codes_are_stripped():
    assert strip_control_sequences("\x1b[31mERROR\x1b[0m here") == "ERROR here"


def test_cursor_and_progress_control_chars_are_stripped():
    assert strip_control_sequences("done\rredone\x08\x08x") == "doneredonex"


def test_osc_sequences_are_stripped():
    assert strip_control_sequences("\x1b]0;title\x07text") == "text"


def test_trailing_whitespace_and_blank_runs_collapse():
    assert normalize_whitespace("a   \n\n\n\nb") == "a\n\nb"


def test_repeated_lines_collapse_with_a_visible_count():
    out = collapse_repeats("x\n" * 6)
    assert "repeated" in out
    assert out.count("x") == 1


def test_short_runs_are_left_intact():
    """Two identical lines is not noise worth a marker."""
    assert collapse_repeats("x\nx\ny") == "x\nx\ny"


# --- what it must never do ---------------------------------------------------


def test_no_line_of_content_is_ever_dropped():
    """The whole contract: nothing the payload says may disappear."""
    body = "\n".join(f"unique line number {i}" for i in range(80))
    out = compress_plain_text(_long(body))
    for i in range(80):
        assert f"unique line number {i}" in out


def test_short_payloads_are_untouched():
    text = "brief output\n"
    assert compress_plain_text(text) == text
    assert len(text) < MIN_LENGTH


def test_already_clean_text_is_returned_unchanged():
    text = _long("clean line one\nclean line two")
    assert compress_plain_text(text) == text


def test_never_returns_something_longer():
    for body in ("plain", "a\nb\nc", "\x1b[31mx\x1b[0m"):
        text = _long(body)
        assert len(compress_plain_text(text)) <= len(text)


# --- routing -----------------------------------------------------------------


def test_text_type_routes_here_and_saves_something():
    noisy = _long("\n".join("\x1b[32mok\x1b[0m   " for _ in range(40)))
    result = compress_text(noisy)
    assert result.content_type == "text"
    assert result.tokens_after < result.tokens_before


def test_source_is_still_never_touched():
    """The text path must not become a back door into compressing code."""
    code = _long("def f(x):\n    import os\n    return x")
    result = compress_text(code, file_path="/a/b.py")
    assert result.content_type == "source"
    assert result.text == code


def test_files_are_never_tidied_even_when_classified_as_text():
    """Trailing spaces are a hard line break in Markdown.

    Regression: stripping them rewrote README.md and changed its rendering.
    A payload carrying a filename is a file, not machine output.
    """
    md = _long("A line ending in a hard break  \nnext line")
    result = compress_text(md, file_path="/docs/README.md")
    assert result.text == md
    assert "break  \n" in result.text


def test_pathless_output_is_still_tidied():
    noisy = _long("\n".join("\x1b[32mok\x1b[0m   " for _ in range(40)))
    assert compress_text(noisy).tokens_after < compress_text(noisy).tokens_before
