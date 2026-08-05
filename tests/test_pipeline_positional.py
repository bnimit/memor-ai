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


def test_already_compressed_payloads_are_not_recounted(store, older_enabled):
    """Our own prior output must not inflate the denominator.

    Once older turns are in scope, a payload compressed in request N reappears
    in request N+1 with its marker. Re-measuring it makes the realized-savings
    rate fall the more successfully compression works.
    """
    from memor.proxy.pipeline import already_compressed

    marked = "[memor:ccr:deadbeef]\n" + BIG_MODULE
    body = _body([("t1", "/a.py", marked), ("t2", "/a.py", BIG_MODULE)])
    result = run_pipeline("anthropic", body, store)

    assert already_compressed(marked)
    # The marked payload is passed through untouched...
    assert _texts(result.body)[0] == marked
    # ...and contributes nothing to the accounting.
    assert result.tokens_before < len(marked)


def test_marker_detection_tolerates_leading_whitespace():
    from memor.proxy.pipeline import already_compressed

    assert already_compressed("\n  [memor:ccr:abc]\nrest") is True
    assert already_compressed("def f(): pass") is False
    assert already_compressed("") is False


def test_a_locked_ledger_does_not_discard_compression(store, older_enabled, monkeypatch):
    """Bookkeeping failure must not cost the user the rewrite.

    Observed live: 356 'database is locked' errors while the daemon ingested,
    each one propagating out of the pipeline into the shim's fail-open, so 219
    consecutive requests forwarded uncompressed despite compression succeeding.
    """
    import sqlite3

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "record_proxy_savings", boom)
    body = _body([("t1", "/a.py", BIG_MODULE), ("t2", "/a.py", BIG_MODULE)])
    result = run_pipeline("anthropic", body, store)
    assert result.tokens_after < result.tokens_before


def test_unstorable_blob_leaves_the_payload_verbatim(store, older_enabled, monkeypatch):
    """A marker promises the original is retrievable; do not promise falsely."""
    import sqlite3

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "ccr_put", boom)
    body = _body([("t1", "/a.py", BIG_MODULE), ("t2", "/a.py", BIG_MODULE)])
    result = run_pipeline("anthropic", body, store)
    for text in _texts(result.body):
        assert "[memor:ccr:" not in text, "dangling marker with no stored blob"
    assert result.passthrough is True
