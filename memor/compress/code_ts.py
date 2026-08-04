"""Multi-language code skeletonization via tree-sitter.

Python is handled by the stdlib ``ast`` in ``memor.compress.code`` and needs no
dependency. This covers the rest, and on real traffic the rest is most of it:
Go 23%, TypeScript+TSX 25%, and Rust 9% of file-read payload against Python's
10%.

Same contract as the Python path — cut only at syntactic boundaries, keep every
signature reachable, and verify the result before returning it. Tree-sitter is
error-tolerant rather than strict, so the gate compares error-node counts before
and after: a skeleton that introduces new parse errors is discarded.

The dependency is optional. Without it, these languages pass through exactly as
they do today.
"""
from __future__ import annotations

from functools import lru_cache

#: Nodes whose body may be elided. Deliberately explicit: matching any node with
#: a "body" field would also catch loops and blocks, which is not the intent.
_FUNCTION_TYPES = frozenset({
    # go
    "function_declaration", "method_declaration",
    # rust
    "function_item",
    # typescript / javascript
    "method_definition", "arrow_function", "function_expression",
    "generator_function_declaration", "function_signature",
})

#: Body containers we know how to elide the interior of.
_BODY_TYPES = frozenset({"block", "statement_block"})

LANGUAGE_BY_EXT = {
    ".go": "go",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
}

#: Bodies shorter than this are not worth a placeholder.
MIN_BODY_LINES = 3

PLACEHOLDER = "/* memor: {n} lines omitted */"


def language_for_path(file_path: str | None) -> str | None:
    if not file_path:
        return None
    lowered = file_path.lower()
    for ext, lang in LANGUAGE_BY_EXT.items():
        if lowered.endswith(ext):
            return lang
    return None


@lru_cache(maxsize=1)
def available() -> bool:
    """True when tree-sitter and the grammar pack are importable."""
    try:
        import tree_sitter_language_pack  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=16)
def _parser(language: str):
    from tree_sitter_language_pack import get_parser

    return get_parser(language)


def _error_count(node) -> int:
    """Number of error or missing nodes in a tree."""
    count = 0
    stack = [node]
    while stack:
        n = stack.pop()
        if n.is_error or n.is_missing:
            count += 1
        stack.extend(n.children)
    return count


def _holds_function(node) -> bool:
    """True when this subtree defines further functions.

    Eliding such a body destroys the structural map — the same failure that made
    a FastAPI ``create_app`` swallow every route handler on the Python path.
    """
    stack = list(node.children)
    while stack:
        n = stack.pop()
        if n.type in _FUNCTION_TYPES:
            return True
        stack.extend(n.children)
    return False


def _regions(root) -> list[tuple[int, int, int, int]]:
    """Byte ranges to elide, as (start, end, line_count, indent_col)."""
    out: list[tuple[int, int, int, int]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in _FUNCTION_TYPES:
            body = node.child_by_field_name("body")
            if body is not None and body.type in _BODY_TYPES:
                if _holds_function(body):
                    stack.extend(body.children)
                    continue
                lines = body.end_point[0] - body.start_point[0]
                if lines >= MIN_BODY_LINES:
                    # Interior only, so the braces survive and the file parses.
                    # The interior includes the indentation before the closing
                    # brace, so that has to be restored or the brace unindents.
                    out.append(
                        (body.start_byte + 1, body.end_byte - 1, lines,
                         node.start_point[1])
                    )
                    continue
        stack.extend(node.children)
    return out


def skeletonize(source: str, language: str) -> str:
    """Return a structure-preserving skeleton, or the input if that is not safe."""
    if not source.strip() or not available():
        return source
    try:
        parser = _parser(language)
    except Exception:
        return source

    data = source.encode("utf-8", "surrogatepass")
    try:
        tree = parser.parse(data)
    except Exception:
        return source

    before_errors = _error_count(tree.root_node)
    regions = _regions(tree.root_node)
    if not regions:
        return source

    # Apply back-to-front so earlier offsets stay valid.
    out = bytearray(data)
    for start, end, lines, indent in sorted(regions, key=lambda r: r[0], reverse=True):
        if start >= end:
            continue
        pad = " " * indent
        replacement = (
            f"\n{pad}    {PLACEHOLDER.format(n=lines)}\n{pad}"
        ).encode("utf-8")
        out[start:end] = replacement

    try:
        skeleton = out.decode("utf-8", "surrogatepass")
    except Exception:
        return source

    # The gate: never introduce parse errors that were not already there.
    try:
        after = parser.parse(bytes(out))
    except Exception:
        return source
    if _error_count(after.root_node) > before_errors:
        return source
    return skeleton
