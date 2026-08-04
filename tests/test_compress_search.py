"""Search-result folding.

Matches are the answer to the query, so the contract is stricter than for logs:
every file, every line number and every matched string survives. Only the
structural repetition — the path on every line, the same content at several
locations — is folded away.
"""
from __future__ import annotations

import re

from memor.compress import compress_text
from memor.compress.search import MIN_LINES, compress_search


def _grep(path: str, pairs: list[tuple[int, str]]) -> str:
    return "\n".join(f"{path}:{n}:{c}" for n, c in pairs)


MANY = _grep("src/app.py", [(i, f"    value_{i} = compute({i})") for i in range(40)])


# --- nothing may be lost -----------------------------------------------------


def test_every_line_number_survives():
    out = compress_search(MANY)
    for i in range(40):
        assert str(i) in out, f"line number {i} was dropped"


def test_every_distinct_match_survives():
    out = compress_search(MANY)
    for i in range(40):
        assert f"value_{i}" in out


def test_every_file_survives():
    text = "\n".join(
        _grep(f"pkg/mod{f}.go", [(n, f"func Handler{n}()") for n in range(5)])
        for f in range(6)
    )
    out = compress_search(text)
    for f in range(6):
        assert f"pkg/mod{f}.go" in out


def test_duplicate_content_folds_into_one_line_with_all_locations():
    text = _grep("a.py", [(n, "import os") for n in (3, 9, 17, 42)])
    text += "\n" + _grep("a.py", [(n, f"x = {n}") for n in range(10)])
    out = compress_search(text)
    assert out.count("import os") == 1
    for n in (3, 9, 17, 42):
        assert str(n) in out


# --- it must actually compress ----------------------------------------------


def test_real_shaped_results_compress():
    out = compress_search(MANY)
    assert len(out) < len(MANY)


def test_repeated_paths_are_folded_once():
    out = compress_search(MANY)
    assert out.count("src/app.py") == 1


def test_grep_context_separator_is_understood():
    """grep uses `-` for context lines around a match."""
    text = "\n".join(f"a.py-{n}-context line {n}" for n in range(12))
    assert len(compress_search(text)) < len(text)


# --- restraint ---------------------------------------------------------------


def test_short_payloads_are_untouched():
    text = _grep("a.py", [(1, "x"), (2, "y")])
    assert compress_search(text) == text
    assert text.count("\n") + 1 <= MIN_LINES


def test_unstructured_text_is_left_alone():
    """Regression: it used to sort by length and keep the 20 longest lines."""
    prose = "\n".join(f"This is ordinary prose line number {i}." for i in range(40))
    assert compress_search(prose) == prose


def test_mixed_payload_without_enough_matches_is_left_alone():
    text = "\n".join([f"note line {i}" for i in range(30)] + ["a.py:1:hit"])
    assert compress_search(text) == text


def test_never_returns_something_longer():
    for text in (MANY, "a.py:1:x\n" * 9, "\n".join("short" for _ in range(20))):
        assert len(compress_search(text)) <= len(text)


def test_ordering_is_preserved():
    """Sorting by length used to scramble results; order carries meaning."""
    text = _grep("a.py", [(n, f"line {n}") for n in range(20)])
    out = compress_search(text)
    positions = [out.index(f"line {n}") for n in range(20)]
    assert positions == sorted(positions)


# --- routing -----------------------------------------------------------------


def test_search_type_routes_here():
    result = compress_text(MANY)
    assert result.content_type == "search"
    assert result.tokens_after < result.tokens_before
