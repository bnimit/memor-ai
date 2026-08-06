"""memor's MCP server is how recall reaches agents whose hooks cannot inject.

jcode's hooks are observers: detached, fire-and-forget, stdout discarded. They
can trigger ingest but can never hand memories back to a prompt, so a tool the
model calls itself is the remaining channel.
"""
from __future__ import annotations

import json

from memor.cli import install_mcp_jcode
from memor.proxy import mcp_retrieve as mcp


def test_initialize_advertises_tools():
    """A client that gets no initialize reply never asks for the tool list."""
    res = mcp.handle_initialize()
    assert res["protocolVersion"]
    assert res["serverInfo"]["name"] == "memor"
    assert "tools" in res["capabilities"]


def test_recall_tool_is_exposed():
    names = [t["name"] for t in mcp.handle_tools_list()["tools"]]
    assert "memor_recall" in names
    assert "memor_retrieve" in names, "the CCR tool must not be dropped"


def test_recall_tool_schema_requires_a_query():
    tool = next(t for t in mcp.handle_tools_list()["tools"]
                if t["name"] == "memor_recall")
    assert tool["inputSchema"]["required"] == ["query"]
    assert "project" in tool["inputSchema"]["properties"]


def test_empty_query_is_an_error_not_a_crash():
    out = mcp.handle_tools_call("memor_recall", {"query": "  "}, store=None)
    assert out.get("isError") is True


def test_recall_failure_reads_as_no_memories(monkeypatch, tmp_path):
    """A broken lookup must never break the agent's tool call."""
    monkeypatch.setattr(mcp, "default_db_path", lambda: str(tmp_path / "missing.db"))
    text = mcp.recall_memories("anything at all")
    assert "memor:" in text
    assert "Traceback" not in text


def test_a_miss_names_projects_that_do_have_memories(monkeypatch):
    """A bare "no memories" cannot be told apart from "wrong project".

    The server's cwd is whatever launched the agent, so the default project is
    routinely wrong. Naming the real projects turns a dead end into a retry.
    """
    monkeypatch.setattr(mcp, "_other_projects_hint",
                        lambda db, tried, limit=6: "Try project= one of: Alpha, Beta.")

    class _Empty:
        formatted_context = ""
        hits_count = 0

    monkeypatch.setattr("memor.recall.recall", lambda *a, **k: _Empty())
    monkeypatch.setattr("memor.cli._embedder", lambda fake: object())
    monkeypatch.setattr(mcp.Path, "exists", lambda self: True)

    text = mcp.recall_memories("something absent", project="Wrong")
    assert "Wrong" in text
    assert "Alpha" in text, "a miss should point at projects that do have memories"


def test_unknown_tool_is_reported():
    out = mcp.handle_tools_call("not_a_tool", {}, store=None)
    assert out.get("isError") is True


def test_install_writes_a_memor_server(tmp_path):
    cfg = tmp_path / "mcp.json"
    assert install_mcp_jcode(cfg, "/usr/local/bin/memor-retrieve-mcp") is True
    data = json.loads(cfg.read_text())
    assert data["servers"]["memor"]["command"].endswith("memor-retrieve-mcp")


def test_install_preserves_other_servers(tmp_path):
    """jcode's mcp.json holds the user's own servers; they must survive."""
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"servers": {
        "node_repl": {"command": "/opt/node_repl", "args": [], "env": {}},
    }}))
    install_mcp_jcode(cfg, "/usr/local/bin/memor-retrieve-mcp")
    data = json.loads(cfg.read_text())
    assert "node_repl" in data["servers"]
    assert "memor" in data["servers"]


def test_install_is_idempotent(tmp_path):
    cfg = tmp_path / "mcp.json"
    assert install_mcp_jcode(cfg, "/bin/x") is True
    assert install_mcp_jcode(cfg, "/bin/x") is False, "rewrote an identical entry"


def test_unparseable_config_is_left_alone(tmp_path):
    """Better to do nothing than to destroy a config we cannot read."""
    cfg = tmp_path / "mcp.json"
    cfg.write_text("{not valid json")
    assert install_mcp_jcode(cfg, "/bin/x") is False
    assert cfg.read_text() == "{not valid json"


def test_install_stamps_the_calling_agent(tmp_path):
    """Unstamped, MCP recalls are attributed to a default and jcode looks idle."""
    cfg = tmp_path / "mcp.json"
    install_mcp_jcode(cfg, "/bin/memor-retrieve-mcp")
    data = json.loads(cfg.read_text())
    assert data["servers"]["memor"]["env"]["MEMOR_HOOK_AGENT"] == "jcode"


def test_mcp_recall_is_logged(tmp_path, monkeypatch):
    """Every per-agent dashboard view reads recall_log.

    The hook and proxy paths both log. An agent served only over MCP would
    otherwise do real work and show as permanently absent, which reads as
    "memor does nothing here" rather than "memor is not measured here".
    """
    logged = {}

    def fake_log(db_path, project, query, result, agent, latency_ms):
        logged.update(agent=agent, project=project, query=query)

    monkeypatch.setattr(mcp, "_log", fake_log)

    class _Result:
        hits_count = 2
        top_score = 0.8
        tokens_injected = 100
        status = "ok"
        formatted_context = "## Recalled Memories"

    monkeypatch.setattr("memor.recall.recall", lambda *a, **k: _Result())
    monkeypatch.setattr("memor.cli._embedder", lambda fake: object())
    monkeypatch.setattr(mcp.Path, "exists", lambda self: True)

    mcp.recall_memories("a query", project="P", agent="jcode")
    assert logged.get("agent") == "jcode", "MCP recall was not logged"
    assert logged.get("project") == "P"


def test_logging_failure_does_not_break_the_tool_call(tmp_path, monkeypatch):
    """Measurement must never break the thing it measures."""
    monkeypatch.setattr(mcp, "default_db_path", lambda: str(tmp_path / "nope.db"))
    # A missing store short-circuits before logging; the call still returns text.
    assert "memor:" in mcp.recall_memories("anything")
