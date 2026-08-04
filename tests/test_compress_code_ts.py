"""Multi-language skeletonization via tree-sitter.

Python is 10% of real file-read payload; Go, TypeScript and Rust together are
57%. Same contract as the Python path — cut at syntactic boundaries, keep every
signature, and never introduce a parse error that was not already there.
"""
from __future__ import annotations

import pytest

from memor.compress.code import compress_code, compressible_language
from memor.compress.code_ts import (
    available,
    language_for_path,
    skeletonize,
)

pytestmark = pytest.mark.skipif(
    not available(), reason="tree-sitter not installed (optional extra)"
)

GO = '''package main

import "fmt"

func Add(a, b int) int {
\tx := a
\ty := b
\tz := x + y
\treturn z
}

type Server struct{ Port int }

func (s *Server) Start() error {
\tfmt.Println("starting")
\tfmt.Println(s.Port)
\tfmt.Println("done")
\treturn nil
}
'''

TS = '''export interface Cfg { host: string; port: number }

export function connect(cfg: Cfg): Promise<void> {
  const a = cfg.host;
  const b = cfg.port;
  const c = a + b;
  return open(c);
}

export class Client {
  async send(msg: string): Promise<number> {
    const x = 1;
    const y = 2;
    const z = x + y;
    return z;
  }
}
'''

RUST = '''pub struct Cfg { pub port: u16 }

pub fn add(a: i32, b: i32) -> i32 {
    let x = a;
    let y = b;
    let z = x + y;
    z
}
'''


def _errors(src: str, lang: str) -> int:
    from memor.compress.code_ts import _error_count, _parser

    return _error_count(_parser(lang).parse(src.encode()).root_node)


# --- the guarantee -----------------------------------------------------------


@pytest.mark.parametrize("src,lang", [(GO, "go"), (TS, "typescript"), (RUST, "rust")])
def test_no_new_parse_errors(src, lang):
    assert _errors(skeletonize(src, lang), lang) <= _errors(src, lang)


@pytest.mark.parametrize("src,lang", [(GO, "go"), (TS, "typescript"), (RUST, "rust")])
def test_skeleton_is_smaller(src, lang):
    assert len(skeletonize(src, lang)) < len(src)


# --- what must survive -------------------------------------------------------


def test_go_signatures_and_types_survive():
    out = skeletonize(GO, "go")
    assert "func Add(a, b int) int" in out
    assert "func (s *Server) Start() error" in out
    assert "type Server struct{ Port int }" in out
    assert 'import "fmt"' in out
    assert "fmt.Println" not in out


def test_typescript_signatures_and_interfaces_survive():
    out = skeletonize(TS, "typescript")
    assert "export interface Cfg" in out
    assert "export function connect(cfg: Cfg): Promise<void>" in out
    assert "async send(msg: string): Promise<number>" in out
    assert "const x = 1" not in out


def test_rust_signature_and_struct_survive():
    out = skeletonize(RUST, "rust")
    assert "pub struct Cfg" in out
    assert "pub fn add(a: i32, b: i32) -> i32" in out
    assert "let x = a" not in out


def test_placeholder_reports_line_count():
    out = skeletonize(GO, "go")
    assert "memor:" in out and "lines omitted" in out


def test_closing_brace_keeps_its_indentation():
    """The body interior includes the whitespace before `}` — restore it."""
    out = skeletonize(TS, "typescript")
    assert "\n  }" in out, "method closing brace lost its indent"


# --- restraint ---------------------------------------------------------------


def test_short_bodies_are_left_alone():
    src = "func f() int {\n\treturn 1\n}\n"
    assert skeletonize(src, "go") == src


def test_nested_functions_are_not_swallowed():
    src = '''export function outer() {
  const helper = (a: number) => {
    const x = a;
    const y = x + 1;
    const z = y + 2;
    return z;
  };
  return helper;
}
'''
    out = skeletonize(src, "typescript")
    assert "helper" in out


def test_unparseable_input_is_returned_unchanged():
    junk = "func ( { { { unbalanced\n"
    assert skeletonize(junk, "go") == junk


def test_empty_input_passes_through():
    assert skeletonize("", "go") == ""
    assert skeletonize("   \n", "go") == "   \n"


def test_deterministic_and_idempotent():
    once = skeletonize(GO, "go")
    assert skeletonize(GO, "go") == once
    assert skeletonize(once, "go") == once


# --- routing -----------------------------------------------------------------


@pytest.mark.parametrize(
    "path,lang",
    [("/a/b.go", "go"), ("/a/b.ts", "typescript"), ("/a/b.tsx", "tsx"),
     ("/a/b.rs", "rust"), ("/a/b.js", "javascript"), ("/a/b.txt", None)],
)
def test_language_for_path(path, lang):
    assert language_for_path(path) == lang


def test_compressible_language_prefers_python_without_the_dependency():
    assert compressible_language("/a/b.py") == "python"


def test_compressible_language_rejects_unknown_extensions():
    assert compressible_language("/a/b.md") is None
    assert compressible_language(None) is None


def test_compress_code_routes_by_path():
    out = compress_code(GO, file_path="/x/main.go")
    assert "func Add(a, b int) int" in out
    assert "z := x + y" not in out


def test_compress_code_leaves_unknown_languages_alone():
    text = "some prose about code\n" * 20
    assert compress_code(text, file_path="/x/notes.md") == text
