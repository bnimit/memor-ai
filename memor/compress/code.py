"""Structure-preserving code compression.

Research on SWE-bench Verified found that the dominant failure mode when
compressing code context is **disrupted syntactic structure** — 17 of 30
inspected failures, more than scoring errors and distraction combined. Token- and
line-level compressors fragment code; AST-boundary compression does not.

So this cuts only at statement boundaries and keeps the shape of the file:
imports, class and function signatures, decorators, docstrings, and
module/class-level assignments (dataclass fields, constants, config) all survive.
Only function *bodies* are elided, replaced with ``...`` and a line count.

The result is re-parsed before it is returned. If the skeleton does not parse,
the original is returned unchanged — a missed saving is cheap, a fragmented file
is not.

Deliberately query-agnostic: the strongest published methods score segments
against the user's question, which forfeits prompt-cache reuse and costs seconds
per request. Both are disqualifying for an inline proxy, so this trades peak
compression for determinism and speed.
"""
from __future__ import annotations

import ast
import re

#: Functions with bodies at or below this many lines are left alone — the
#: placeholder would not pay for itself.
MIN_BODY_LINES = 3

PLACEHOLDER = "...  # memor: {n} lines omitted"


def _body_region(node: ast.AST) -> tuple[int, int, str] | None:
    """Line range of a function body to elide, as (start, end_exclusive, indent).

    Docstrings are preserved: they are the densest description of what the
    elided code does, and are what makes a skeleton readable at all.
    """
    body = getattr(node, "body", None)
    if not body:
        return None

    first = body[0]
    start = first.lineno - 1
    # Keep a leading docstring in place; elide everything after it.
    if (
        isinstance(first, ast.Expr)
        and isinstance(getattr(first, "value", None), ast.Constant)
        and isinstance(first.value.value, str)
    ):
        if len(body) == 1:
            return None
        start = (first.end_lineno or first.lineno)

    last = body[-1]
    end = last.end_lineno or last.lineno
    if end - start < MIN_BODY_LINES:
        return None
    return start, end, " " * first.col_offset


_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _holds_definitions(node: ast.AST) -> bool:
    """True when this function's body defines further functions or classes.

    Eliding such a body wholesale destroys the structural map the skeleton
    exists to provide — a FastAPI ``create_app`` holding twenty route handlers
    collapses to a single placeholder, and the reader cannot tell the routes
    ever existed. Recurse into these and elide the leaves instead.
    """
    return any(isinstance(child, _DEF_TYPES) for child in getattr(node, "body", []))


def _collect_regions(tree: ast.AST) -> list[tuple[int, int, str]]:
    """Body regions to elide, keeping every signature reachable.

    Class bodies are always walked so methods are found. Function bodies are
    elided only when they are leaves; a body that defines more functions is
    descended into so those signatures survive.
    """
    regions: list[tuple[int, int, str]] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _holds_definitions(child):
                    visit(child)
                    continue
                region = _body_region(child)
                if region:
                    regions.append(region)
                else:
                    # Body kept whole; a nested def inside it may still be large.
                    visit(child)
            else:
                visit(child)

    visit(tree)
    return regions


#: Agent file readers prefix each line with its number and a tab. The result is
#: not valid source in any language, so a strict parser rejects the whole file
#: and the compressor silently does nothing — which is exactly what was
#: happening to every Python payload in real traffic.
_GUTTER = re.compile(r"^(\s*\d+)\t")


def split_line_gutter(source: str) -> tuple[str, list[str] | None]:
    """Separate a line-number gutter from the code, if one is present.

    Returns ``(code, numbers)``; ``numbers`` is None when there is no gutter.
    Requires most lines to match so a stray "1\\tfoo" in real source does not
    trigger it.
    """
    lines = source.split("\n")
    matches = [_GUTTER.match(ln) for ln in lines]
    hits = sum(1 for m in matches if m)
    substantive = sum(1 for ln in lines if ln.strip())
    if not substantive or hits < substantive * 0.8:
        return source, None

    numbers: list[str] = []
    code: list[str] = []
    for ln, m in zip(lines, matches):
        if m:
            numbers.append(m.group(1))
            code.append(ln[m.end():])
        else:
            numbers.append("")
            code.append(ln)
    return "\n".join(code), numbers


def _apply_gutter(rendered: list[tuple[int | None, str]], numbers: list[str]) -> str:
    """Re-attach original line numbers to the lines that survived.

    Numbering stays true to the source rather than being resequenced, so a line
    the agent sees as 412 really is line 412 in the file. Placeholders get no
    number — they stand for a range, not a line.
    """
    out = []
    for idx, text in rendered:
        if idx is None or idx >= len(numbers) or not numbers[idx]:
            out.append(text)
        else:
            out.append(f"{numbers[idx]}\t{text}")
    return "\n".join(out)


def restore_gutter(clean: str, skeleton: str, numbers: list[str]) -> str:
    """Re-attach line numbers to a skeleton produced from gutter-stripped code.

    Used by parsers that rebuild text rather than track line indices. Walks both
    sides in order: a skeleton line that matches the next unconsumed original
    keeps that original's number, and anything else (a placeholder) gets none.
    """
    original = clean.split("\n")
    out: list[str] = []
    cursor = 0
    for line in skeleton.split("\n"):
        probe = cursor
        while probe < len(original) and original[probe] != line:
            probe += 1
        if probe < len(original):
            number = numbers[probe] if probe < len(numbers) else ""
            out.append(f"{number}\t{line}" if number else line)
            cursor = probe + 1
        else:
            out.append(line)
    return "\n".join(out)


def skeletonize_python(source: str) -> str:
    """Return a structure-preserving skeleton, or the input if that is not safe."""
    if not source.strip():
        return source

    code, numbers = split_line_gutter(source)
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return source

    regions = _collect_regions(tree)
    if not regions:
        return source

    lines = code.split("\n")
    drop: dict[int, tuple[int, str]] = {}
    for start, end, indent in regions:
        drop[start] = (end, indent)

    rendered: list[tuple[int | None, str]] = []
    i = 0
    while i < len(lines):
        if i in drop:
            end, indent = drop[i]
            rendered.append((None, f"{indent}{PLACEHOLDER.format(n=end - i)}"))
            i = end
            continue
        rendered.append((i, lines[i]))
        i += 1

    skeleton = "\n".join(text for _, text in rendered)

    # The gate: a skeleton that does not parse is worse than no compression.
    try:
        ast.parse(skeleton)
    except (SyntaxError, ValueError, RecursionError):
        return source

    return _apply_gutter(rendered, numbers) if numbers else skeleton


def compress_code(
    text: str, *, language: str | None = None, file_path: str | None = None
) -> str:
    """Compress source code, choosing a parser from the language or path.

    Python goes through the stdlib ``ast`` and needs no dependency. Go,
    TypeScript, TSX, JavaScript, and Rust go through tree-sitter when it is
    installed, and pass through untouched when it is not. A language neither
    path can parse is a language neither can safely cut, so it is returned as-is.
    """
    if language == "python" or (language is None and looks_like_python(text, file_path)):
        return skeletonize_python(text)

    from memor.compress.code_ts import language_for_path, skeletonize

    lang = language or language_for_path(file_path)
    if not lang:
        return text
    return skeletonize(text, lang)


def compressible_language(file_path: str | None) -> str | None:
    """Language name if this path is one we can skeletonize, else None."""
    if file_path and file_path.lower().endswith((".py", ".pyi")):
        return "python"
    from memor.compress.code_ts import available, language_for_path

    lang = language_for_path(file_path)
    return lang if (lang and available()) else None


def looks_like_python(text: str, file_path: str | None = None) -> bool:
    """Cheap check before paying for a parse."""
    if file_path and file_path.endswith((".py", ".pyi")):
        return True
    if file_path:
        return False
    head = text[:4000]
    return ("def " in head or "class " in head or "import " in head) and (
        ":" in head
    )
