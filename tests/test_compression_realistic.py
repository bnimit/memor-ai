"""End-to-end behaviour on realistic payloads, not toy strings.

Unit tests confirmed each compressor does what it says. These confirm the thing
a user actually experiences: a real agent request carrying a mix of payloads,
and every real file in this repository, none of which may come back damaged.

The corpus is deliberately this repo's own files — including the two that were
genuinely being shredded in production (``dashboard/static/index.html`` and
``service.py``), so the regression is pinned against the real article rather
than a reconstruction.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from memor.compress import compress_text
from memor.compress.code import skeletonize_python
from memor.compress.detect import detect_content_type
from memor.proxy.pipeline import run_pipeline
from memor.store.sqlite_store import SqliteStore

REPO = Path(__file__).resolve().parent.parent

BASH_LOG = "\n".join(
    f"2026-08-04 10:00:{i % 60:02d} INFO worker heartbeat ok id={i}" for i in range(400)
)
GREP_OUT = "\n".join(f"memor/mod{i}.py:{i}:    return value_{i}" for i in range(200))
API_JSON = json.dumps({"data": {"results": [{"id": i, "name": f"row{i}"} for i in range(300)]}})
PY_FILE = (REPO / "memor" / "episodes.py").read_text()
def _go_source(n_funcs: int = 12) -> str:
    """A Go file large enough that a compression marker pays for itself."""
    head = 'package main\n\nimport (\n\t"fmt"\n\t"errors"\n)\n\n'
    body = ""
    for i in range(n_funcs):
        body += (
            f"// Handle{i} processes a request.\n"
            f"func Handle{i}(req *Request) (*Response, error) {{\n"
            f"\ta := req.Body\n"
            f"\tb := parse(a)\n"
            f"\tif b == nil {{\n"
            f"\t\treturn nil, errors.New(\"bad body {i}\")\n"
            f"\t}}\n"
            f"\tc := validate(b)\n"
            f"\tfmt.Println(c, {i})\n"
            f"\treturn build(c), nil\n"
            f"}}\n\n"
        )
    return head + body


GO_FILE = _go_source()


@pytest.fixture
def store(tmp_path):
    return SqliteStore(str(tmp_path / "r.db"), dim=16)


def _read(uid, path, text):
    return (
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": uid, "name": "Read", "input": {"file_path": path}}
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": uid, "content": text}
        ]},
    )


def _bash(uid, text):
    return (
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": uid, "name": "Bash", "input": {"command": "make test"}}
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": uid, "content": text}
        ]},
    )


def _payload_texts(body):
    out = []
    for m in body["messages"]:
        if m.get("role") != "user":
            continue
        for b in m.get("content", []):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                out.append(b["content"])
    return out


# --- a realistic agent turn --------------------------------------------------


def test_mixed_agent_request_is_handled_per_payload(store, monkeypatch):
    """One request carrying everything an agent actually collects."""
    monkeypatch.setenv("MEMOR_COMPRESS_OLDER", "1")
    pairs = [
        _read("t1", "/proj/episodes.py", PY_FILE),      # stale code read
        _bash("t2", BASH_LOG),                           # shell output
        _read("t3", "/proj/README.md", "# Title\n\nSome prose.\n" * 40),
        _bash("t4", GREP_OUT),                           # grep results
        _read("t5", "/proj/episodes.py", PY_FILE),       # newest read of same file
    ]
    messages = [m for pair in pairs for m in pair]
    result = run_pipeline("anthropic", {"messages": messages}, store)
    texts = _payload_texts(result.body)

    stale_code, shell, markdown, grep, newest_code = texts

    # The newest read of a file the agent may edit is untouched.
    assert newest_code == PY_FILE
    # The stale read of the same file is skeletonized, and still parses.
    assert stale_code != PY_FILE
    ast.parse("\n".join(stale_code.split("\n")[1:]))
    # Machine output is crushed.
    assert len(shell) < len(BASH_LOG)
    assert len(grep) < len(GREP_OUT)
    # Prose is left alone.
    assert markdown.startswith("# Title")
    assert result.tokens_after < result.tokens_before


def test_a_turn_with_nothing_compressible_is_a_clean_passthrough(store):
    """The common case: no payload worth touching, nothing modified."""
    pairs = [_read("t1", "/proj/a.md", "# Notes\n\nShort.\n")]
    messages = [m for pair in pairs for m in pair]
    body = {"messages": messages}
    result = run_pipeline("anthropic", body, store)
    assert result.passthrough is True
    assert _payload_texts(result.body) == ["# Notes\n\nShort.\n"]


# --- the corpus: nothing in this repo may be damaged ------------------------


def _repo_files(*globs):
    out = []
    for g in globs:
        out.extend(sorted(REPO.glob(g)))
    return [p for p in out if p.is_file() and p.stat().st_size < 400_000]


@pytest.mark.parametrize(
    "path", [p for p in _repo_files("memor/**/*.py", "memor/**/*.html", "*.md")]
)
def test_no_real_repo_file_is_altered_by_compress_text(path):
    """With the filename known, a source file must come back byte-identical."""
    text = path.read_text()
    result = compress_text(text, file_path=str(path))
    assert result.text == text, f"{path.name} was modified"
    assert len(result.text.splitlines()) == len(text.splitlines())


def test_the_stylesheet_that_was_actually_being_shredded():
    """Regression on the real file: `var(--warn)` made this read as a log.

    Before the fix this returned 471 of 16,512 tokens with 1,303 lines gone.
    """
    path = REPO / "memor" / "dashboard" / "static" / "index.html"
    text = path.read_text()
    assert "var(--warn)" in text, "fixture no longer reproduces the trigger"
    assert detect_content_type(text, str(path)) != "log"
    assert compress_text(text, file_path=str(path)).text == text


def test_the_service_module_that_was_actually_being_shredded():
    """Before the fix this returned 580 of 3,977 tokens, 399 lines gone."""
    path = REPO / "memor" / "service.py"
    text = path.read_text()
    assert compress_text(text, file_path=str(path)).text == text


@pytest.mark.parametrize("path", _repo_files("memor/**/*.py"))
def test_every_python_file_skeletonizes_to_valid_python(path):
    ast.parse(skeletonize_python(path.read_text()))


# --- detection is evidence-led where evidence exists ------------------------


@pytest.mark.parametrize(
    "path,expected",
    [("/a/b.py", "source"), ("/a/b.go", "source"), ("/a/b.css", "source"),
     ("/a/b.md", "text"), ("/a/b.json", "json"), ("/a/b.log", "log")],
)
def test_filename_decides_over_content(path, expected):
    """A stylesheet full of WARN must not be sniffed as a log."""
    misleading = "\n".join(f".x{i} {{ color: var(--warn); }}" for i in range(50))
    assert detect_content_type(misleading, path) == expected


def test_content_sniffing_still_applies_without_a_filename():
    """Shell output has no file behind it, so heuristics are all there is."""
    assert detect_content_type(BASH_LOG) == "log"
    assert detect_content_type(GREP_OUT) == "search"


def test_go_payload_routes_to_the_treesitter_path(store, monkeypatch):
    from memor.compress.code_ts import available

    if not available():
        pytest.skip("tree-sitter not installed")
    monkeypatch.setenv("MEMOR_COMPRESS_OLDER", "1")
    pairs = [_read("t1", "/proj/main.go", GO_FILE), _read("t2", "/proj/main.go", GO_FILE)]
    messages = [m for pair in pairs for m in pair]
    result = run_pipeline("anthropic", {"messages": messages}, store)
    assert "code:go" in result.content_types
    stale = _payload_texts(result.body)[0]
    # Every signature stays reachable; no implementation does.
    assert "func Handle0(req *Request) (*Response, error)" in stale
    assert "func Handle11(req *Request) (*Response, error)" in stale
    assert "fmt.Println" not in stale
