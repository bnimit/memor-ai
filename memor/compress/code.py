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


def skeletonize_python(source: str) -> str:
    """Return a structure-preserving skeleton, or the input if that is not safe."""
    if not source.strip():
        return source
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return source

    regions = _collect_regions(tree)
    if not regions:
        return source

    lines = source.split("\n")
    drop: dict[int, tuple[int, str]] = {}
    for start, end, indent in regions:
        drop[start] = (end, indent)

    out: list[int | str] = []
    i = 0
    while i < len(lines):
        if i in drop:
            end, indent = drop[i]
            omitted = end - i
            out.append(f"{indent}{PLACEHOLDER.format(n=omitted)}")
            i = end
            continue
        out.append(lines[i])
        i += 1

    skeleton = "\n".join(str(x) for x in out)

    # The gate: a skeleton that does not parse is worse than no compression.
    try:
        ast.parse(skeleton)
    except (SyntaxError, ValueError, RecursionError):
        return source
    return skeleton


def compress_code(text: str, *, language: str | None = None) -> str:
    """Compress source code. Currently Python only; other languages pass through.

    Non-Python files are returned untouched rather than guessed at — a language
    this cannot parse is a language it cannot safely cut.
    """
    if language not in (None, "python"):
        return text
    return skeletonize_python(text)


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
