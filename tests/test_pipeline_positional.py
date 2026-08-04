"""Positional compression: skeletonize what the agent has moved past.

The newest read of a file stays byte-exact — it is the one an agent is most
likely about to edit, and editing against elided lines is the failure this
design exists to avoid. Older reads carry the cost, because the whole
trajectory is resent on every step.
"""
from __future__ import annotations

import ast

import pytest

from memor.embed.fake import FakeEmbedder
from memor.proxy.pipeline import run_pipeline
from memor.store.sqlite_store import SqliteStore

BIG_MODULE = '''\
import os


def alpha(x):
    """Do alpha."""
    a = x + 1
    b = a * 2
    c = b - 3
    d = c / 4
    return d


def beta(y):
    """Do beta."""
    a = y + 1
    b = a * 2
    c = b - 3
    d = c / 4
    return d
'''


@pytest.fixture
def store(tmp_path):
    return SqliteStore(str(tmp_path / "p.db"), dim=16)


@pytest.fixture
def older_enabled(monkeypatch):
    monkeypatch.setenv("MEMOR_COMPRESS_OLDER", "1")


@pytest.fixture
def older_disabled(monkeypatch):
    monkeypatch.setenv("MEMOR_COMPRESS_OLDER", "0")


def _body(reads):
    """reads: list of (tool_use_id, file_path, text)."""
    messages = []
    for uid, path, text in reads:
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": uid, "name": "Read", "input": {"file_path": path}}
        ]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": uid, "content": text}
        ]})
    return {"messages": messages}


def _texts(body):
    out = []
    for m in body["messages"]:
        if m.get("role") != "user":
            continue
        for b in m.get("content", []):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                out.append(b["content"])
    return out


# --- the safety property -----------------------------------------------------


def test_newest_read_of_a_file_is_left_byte_exact(store, older_enabled):
    body = _body([
        ("t1", "/a.py", BIG_MODULE),
        ("t2", "/a.py", BIG_MODULE),
    ])
    result = run_pipeline("anthropic", body, store)
    older, newest = _texts(result.body)
    assert newest == BIG_MODULE, "the read the agent may edit against was modified"
    assert older != BIG_MODULE, "the stale read should have been compressed"


def test_a_single_read_is_never_skeletonized(store, older_enabled):
    """One read of a file is by definition the newest one."""
    body = _body([("t1", "/a.py", BIG_MODULE)])
    result = run_pipeline("anthropic", body, store)
    assert _texts(result.body)[0] == BIG_MODULE


def test_recency_is_per_file(store, older_enabled):
    body = _body([
        ("t1", "/a.py", BIG_MODULE),
        ("t2", "/b.py", BIG_MODULE),
    ])
    result = run_pipeline("anthropic", body, store)
    assert _texts(result.body) == [BIG_MODULE, BIG_MODULE]


# --- the flag ----------------------------------------------------------------


def test_disabled_by_default_leaves_older_reads_alone(store, older_disabled):
    body = _body([
        ("t1", "/a.py", BIG_MODULE),
        ("t2", "/a.py", BIG_MODULE),
    ])
    result = run_pipeline("anthropic", body, store)
    assert _texts(result.body) == [BIG_MODULE, BIG_MODULE]


def test_enabling_actually_saves_tokens(store, older_enabled):
    body = _body([
        ("t1", "/a.py", BIG_MODULE),
        ("t2", "/a.py", BIG_MODULE),
    ])
    result = run_pipeline("anthropic", body, store)
    assert result.tokens_after < result.tokens_before
    assert result.passthrough is False


# --- what the compressed payload must still be -------------------------------


def test_compressed_older_read_is_still_valid_python(store, older_enabled):
    body = _body([
        ("t1", "/a.py", BIG_MODULE),
        ("t2", "/a.py", BIG_MODULE),
    ])
    older = _texts(run_pipeline("anthropic", body, store).body)[0]
    # Strip the CCR marker line the pipeline prepends.
    source = "\n".join(older.split("\n")[1:])
    ast.parse(source)
    assert "def alpha" in source and "def beta" in source


def test_code_savings_are_attributed_to_a_code_content_type(store, older_enabled):
    """Attributed per language, so the ledger shows which parser earned what."""
    body = _body([
        ("t1", "/a.py", BIG_MODULE),
        ("t2", "/a.py", BIG_MODULE),
    ])
    result = run_pipeline("anthropic", body, store)
    assert any(k.startswith("code:") for k in result.content_types)
    assert "code:python" in result.content_types


def test_non_python_older_reads_are_not_skeletonized(store, older_enabled):
    js = "function f() {\n  const a = 1;\n  const b = 2;\n  return a + b;\n}\n"
    body = _body([("t1", "/a.js", js), ("t2", "/a.js", js)])
    result = run_pipeline("anthropic", body, store)
    assert _texts(result.body) == [js, js]


def test_unresolvable_origin_is_never_skeletonized(store, older_enabled):
    """No file path means no recency claim, so the payload must be left alone."""
    body = {"messages": [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "ghost", "content": BIG_MODULE}
        ]},
    ]}
    result = run_pipeline("anthropic", body, store)
    assert _texts(result.body)[0] == BIG_MODULE
