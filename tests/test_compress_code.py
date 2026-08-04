"""Structure-preserving code compression.

The dominant documented failure mode for code compression is disrupted
syntactic structure — more than half of inspected SWE-bench failures. These
tests pin the two properties that guard against it: the output always parses,
and every signature stays reachable.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from memor.compress.code import (
    MIN_BODY_LINES,
    compress_code,
    looks_like_python,
    skeletonize_python,
)

REPO = Path(__file__).resolve().parent.parent

SAMPLE = '''\
from __future__ import annotations
import os

MAX_RETRIES = 5
DEFAULTS = {"a": 1, "b": 2}


@dataclass
class Config:
    """Runtime configuration."""

    host: str = "localhost"
    port: int = 8080

    def resolve(self, override: str | None = None) -> str:
        """Return the effective host."""
        if override:
            return override
        value = self.host
        value = value.strip()
        value = value.lower()
        return value


async def fetch(url: str, *, timeout: float = 1.0) -> bytes:
    """Fetch a URL."""
    conn = open_conn(url)
    data = conn.read()
    conn.close()
    validate(data)
    return data
'''


def _parses(src: str) -> bool:
    try:
        ast.parse(src)
        return True
    except SyntaxError:
        return False


# --- the two guarantees ------------------------------------------------------


def test_output_always_parses():
    assert _parses(skeletonize_python(SAMPLE))


@pytest.mark.parametrize(
    "path",
    ["memor/cli.py", "memor/service.py", "memor/store/sqlite_store.py",
     "memor/dashboard/server.py", "memor/episodes.py", "memor/compress/code.py"],
)
def test_real_files_skeletonize_to_valid_python(path):
    src = (REPO / path).read_text()
    assert _parses(skeletonize_python(src))


def test_every_signature_survives():
    out = skeletonize_python(SAMPLE)
    for sig in ("class Config", "def resolve", "async def fetch"):
        assert sig in out


# --- what must be kept -------------------------------------------------------


def test_imports_and_module_constants_are_kept():
    out = skeletonize_python(SAMPLE)
    assert "import os" in out
    assert "MAX_RETRIES = 5" in out
    assert 'DEFAULTS = {"a": 1, "b": 2}' in out


def test_class_level_fields_are_kept():
    """Dataclass fields are interface, not implementation."""
    out = skeletonize_python(SAMPLE)
    assert 'host: str = "localhost"' in out
    assert "port: int = 8080" in out


def test_decorators_are_kept():
    assert "@dataclass" in skeletonize_python(SAMPLE)


def test_docstrings_are_kept():
    out = skeletonize_python(SAMPLE)
    assert "Return the effective host." in out
    assert "Runtime configuration." in out


def test_bodies_are_elided_with_a_line_count():
    out = skeletonize_python(SAMPLE)
    assert "memor:" in out and "lines omitted" in out
    assert "value.strip()" not in out


# --- the structural-map regression -------------------------------------------


def test_nested_definitions_are_not_swallowed():
    """A body full of nested defs must not collapse to one placeholder.

    Regression: FastAPI's create_app held every route handler, and eliding its
    body wholesale hid the entire API surface behind a single line.
    """
    src = '''\
def create_app():
    """Build it."""
    app = App()

    @app.get("/a")
    def route_a():
        x = 1
        y = 2
        z = 3
        return x + y + z

    @app.get("/b")
    def route_b():
        x = 1
        y = 2
        z = 3
        return x - y - z

    return app
'''
    out = skeletonize_python(src)
    assert "def route_a" in out
    assert "def route_b" in out
    assert _parses(out)


def test_real_route_handlers_survive():
    src = (REPO / "memor/dashboard/server.py").read_text()
    out = skeletonize_python(src)
    for route in ("/api/summary", "/api/recall-worth", "/api/agent-desk"):
        assert route in out


# --- restraint ---------------------------------------------------------------


def test_short_bodies_are_left_alone():
    src = "def f():\n    return 1\n"
    assert skeletonize_python(src) == src


def test_docstring_only_body_is_left_alone():
    src = 'def f():\n    """Just a doc."""\n'
    assert skeletonize_python(src) == src


def test_min_body_lines_is_respected():
    body = "\n".join(f"    x{i} = {i}" for i in range(MIN_BODY_LINES - 1))
    src = f"def f():\n{body}\n"
    assert skeletonize_python(src) == src


def test_skeleton_is_smaller_than_source():
    out = skeletonize_python(SAMPLE)
    assert len(out) < len(SAMPLE)


# --- failing safe ------------------------------------------------------------


def test_syntax_error_passes_through_untouched():
    broken = "def f(:\n    pass\n"
    assert skeletonize_python(broken) == broken


def test_empty_input_passes_through():
    assert skeletonize_python("") == ""
    assert skeletonize_python("   \n") == "   \n"


def test_language_without_a_parser_passes_through():
    """A language neither parser handles must be returned untouched."""
    ruby = "def hello\n  a = 1\n  b = 2\n  a + b\nend\n"
    assert compress_code(ruby, file_path="/x/a.rb") == ruby


def test_javascript_is_handled_when_tree_sitter_is_installed():
    from memor.compress.code_ts import available

    js = "function f() {\n  const a = 1;\n  const b = 2;\n  const c = 3;\n  return a;\n}\n"
    out = compress_code(js, language="javascript")
    if available():
        assert "function f()" in out and "const a = 1" not in out
    else:
        assert out == js


def test_prose_is_not_mangled():
    prose = "We refactored the parser today.\nIt reads better now.\n"
    assert skeletonize_python(prose) == prose


# --- determinism, which is what makes caching possible -----------------------


def test_skeletonize_is_deterministic():
    a = skeletonize_python(SAMPLE)
    b = skeletonize_python(SAMPLE)
    assert a == b


def test_skeletonize_is_idempotent():
    once = skeletonize_python(SAMPLE)
    assert skeletonize_python(once) == once


# --- detection ---------------------------------------------------------------


def test_looks_like_python_uses_extension_when_available():
    assert looks_like_python("anything", "/a/b.py") is True
    assert looks_like_python("def f(): pass", "/a/b.ts") is False


def test_looks_like_python_falls_back_to_content():
    assert looks_like_python("def f():\n    return 1\n") is True
    assert looks_like_python("hello world") is False
