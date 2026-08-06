"""No dashboard table may print the raw "_global" scope name.

"_global" is an internal scope identifier. The projects and quality tables
badge it as "Global", but the recalls tables printed it verbatim, so an
underscore-prefixed name leaked into the UI on every globally-scoped recall.
"""
from __future__ import annotations

import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "memor" / "dashboard" / "static" / "index.html"


def _html() -> str:
    return INDEX.read_text()


def test_a_shared_helper_renders_the_project_cell():
    html = _html()
    assert "function projectCell(" in html, "no shared project cell renderer"
    assert "badge-global" in html


def test_no_table_prints_project_verbatim_into_a_cell():
    """A raw `esc(row.project)` in a cell is how the underscore leaked.

    Only unguarded uses count. The projects table writes the same expression as
    the else-branch of an explicit `_global` ternary, which is already correct,
    so lines within a few lines of a `_global` check are excluded.
    """
    lines = _html().splitlines()
    offenders = []
    for i, line in enumerate(lines):
        if not re.search(r"<td[^>]*>'\s*\+\s*esc\(row\.project\)", line):
            continue
        window = "\n".join(lines[max(0, i - 4):i + 1])
        if "_global" in window:
            continue  # guarded by an explicit global check
        offenders.append(line.strip()[:80])
    assert not offenders, (
        f"{len(offenders)} unguarded cell(s) print project verbatim: {offenders}; "
        "use projectCell() so _global renders as a Global badge")


def test_filter_pill_does_not_show_the_raw_scope():
    html = _html()
    assert "projectFilter === '_global' ? 'Global'" in html, \
        "the project filter pill still shows the raw _global name"


def test_project_cell_badges_global_and_escapes_others():
    """The helper must badge _global and keep escaping ordinary names."""
    html = _html()
    start = html.index("function projectCell(")
    body = html[start:start + 400]
    assert "'_global'" in body
    assert "badge badge-global" in body
    assert "esc(project)" in body, "ordinary project names must stay escaped"
