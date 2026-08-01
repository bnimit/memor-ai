"""Tests for proxy memory injection and per-agent hook skip."""
from memor.embed.fake import FakeEmbedder
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact


def test_hook_skips_when_claude_proxied(tmp_path, monkeypatch):
    """Proxied Claude skips hook inject; memory comes from the proxy path."""
    import memor.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)

    cfg.set_proxy_agent("claude", True)
    assert cfg.is_proxy_agent("claude") is True

    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="a1", kind="memory", project="testproj", source="distill",
        text="we decided to use argon2 for password hashing",
        token_count=12, created_at=100.0,
        meta={"mem_type": "decision", "session_id": "s1"}
    )
    s.add_artifacts([art], e.embed([art.text]))

    from memor.hook_server import handle_request
    req = {
        "prompt": "how does password hashing work?",
        "cwd": str(tmp_path / "testproj"),
        "session_id": "test-claude"
    }

    result = handle_request(req, db_path=db_path, embedder=e)
    ctx = result["hookSpecificOutput"]["additionalContext"]

    assert ctx == ""
    assert s.db.execute("SELECT COUNT(*) AS n FROM recall_log").fetchone()["n"] == 0


def test_hook_skips_when_codex_proxied(tmp_path, monkeypatch):
    """Proxied Codex skips hook inject; memory comes from the proxy path."""
    import memor.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)

    cfg.set_proxy_agent("codex", True)
    assert cfg.is_proxy_agent("codex") is True

    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="a1", kind="memory", project="testproj", source="distill",
        text="use redis for caching",
        token_count=12, created_at=100.0,
        meta={"mem_type": "decision", "session_id": "s1"}
    )
    s.add_artifacts([art], e.embed([art.text]))

    from memor.hook_server import handle_request
    req = {
        "prompt": "how does caching work?",
        "cwd": str(tmp_path / "testproj"),
        "session_id": "test-codex",
        "model": "gpt-5.6-sol-medium",
        "turn_id": "turn-123"
    }

    result = handle_request(req, db_path=db_path, embedder=e)
    ctx = result["hookSpecificOutput"]["additionalContext"]

    assert ctx == ""
    assert s.db.execute("SELECT COUNT(*) AS n FROM recall_log").fetchone()["n"] == 0


def test_hook_cursor_still_recalls_when_claude_proxied(tmp_path, monkeypatch):
    """Cursor should still recall even when Claude is proxy-enabled."""
    import memor.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
    
    # Enable proxy for claude (but NOT cursor)
    cfg.set_proxy_agent("claude", True)
    assert cfg.is_proxy_agent("claude") is True
    assert cfg.is_proxy_agent("cursor") is False
    
    # Setup database with some memories
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="a1", kind="memory", project="testproj", source="distill",
        text="we decided to use argon2 for password hashing",
        token_count=12, created_at=100.0,
        meta={"mem_type": "decision", "session_id": "s1"}
    )
    s.add_artifacts([art], e.embed([art.text]))
    
    # Create Cursor request (has cursor_version or beforeSubmitPrompt)
    from memor.hook_server import handle_request
    req = {
        "prompt": "how does password hashing work?",
        "cwd": str(tmp_path / "testproj"),
        "session_id": "test-cursor",
        "cursor_version": "0.43.6"  # Identifies as cursor
    }
    
    result = handle_request(req, db_path=db_path, embedder=e)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    
    # Should NOT be skipped, should have memories
    assert "skipped" not in ctx.lower() or "trivial" in ctx.lower()
    assert "Recalled Memories" in ctx or "no relevant" in ctx.lower()


def test_hook_copilot_never_skips_for_proxy(tmp_path, monkeypatch):
    """Copilot should never skip due to proxy flag."""
    import memor.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)
    
    # Enable proxy for copilot (shouldn't matter)
    cfg.set_proxy_agent("copilot", True)
    
    # Setup database
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="a1", kind="memory", project="testproj", source="distill",
        text="use bcrypt for passwords",
        token_count=12, created_at=100.0,
        meta={"mem_type": "decision", "session_id": "s1"}
    )
    s.add_artifacts([art], e.embed([art.text]))
    
    # Create Copilot request
    from memor.hook_server import handle_request
    req = {
        "prompt": "how does password hashing work?",
        "cwd": str(tmp_path / "testproj"),
        "session_id": "test-copilot",
        "hook_event_name": "userPromptSubmitted"  # Identifies as copilot
    }
    
    result = handle_request(req, db_path=db_path, embedder=e)
    ctx = result["additionalContext"]  # Copilot uses different format
    
    # Should NOT be skipped due to proxy
    assert "proxy path active" not in ctx.lower()


def test_proxy_injects_recalled_memories_markdown(tmp_path):
    """Proxy should inject recalled memories as markdown into user message."""
    # Setup database with memory
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="mem1", kind="memory", project="testproj", source="distill",
        text="we use FastAPI for all REST APIs",
        token_count=12, created_at=100.0,
        meta={"mem_type": "decision", "session_id": "old-session"}
    )
    s.add_artifacts([art], e.embed([art.text]))
    
    # Create Anthropic-style request body
    from memor.proxy.memory import inject_memory
    body = {
        "model": "claude-sonnet-4-20250514",
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "which framework should I use for APIs?"}
        ],
        "max_tokens": 1024
    }
    
    result = inject_memory(
        "anthropic", body,
        project="testproj",
        db_path=db_path,
        embedder=e
    )
    
    # Check that memories were injected into the last user message
    last_user_msg = result["messages"][-1]
    assert last_user_msg["role"] == "user"
    content = last_user_msg["content"]
    
    # Original query should still be there
    assert "which framework should I use for APIs?" in content
    
    # Recalled memories section should be appended
    assert "## Recalled Memories" in content
    assert "FastAPI" in content or "REST APIs" in content


def test_proxy_injects_when_content_is_block_list(tmp_path):
    """Anthropic block-list content must be searched and extended, not stringified."""
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="mem1", kind="memory", project="testproj", source="distill",
        text="we use FastAPI for all REST APIs",
        token_count=12, created_at=100.0,
        meta={"mem_type": "decision", "session_id": "old-session"}
    )
    s.add_artifacts([art], e.embed([art.text]))

    from memor.proxy.memory import inject_memory
    body = {
        "model": "claude-sonnet-4-20250514",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "which framework should I use for APIs?"},
            ]},
        ],
    }

    result = inject_memory("anthropic", body, project="testproj", db_path=db_path, embedder=e)
    content = result["messages"][-1]["content"]

    # Shape preserved: still a block list, with the memories as an extra block.
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "which framework should I use for APIs?"}
    assert "## Recalled Memories" in content[-1]["text"]


def test_proxy_inject_unknown_project_falls_back_to_global(tmp_path):
    """With no project hint, the proxy searches the _global scope."""
    from memor.types import GLOBAL_PROJECT

    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="g1", kind="memory", project=GLOBAL_PROJECT, source="promotion",
        text="always run the linter before committing",
        token_count=12, created_at=100.0,
        meta={"mem_type": "decision"}
    )
    s.add_artifacts([art], e.embed([art.text]))

    from memor.proxy.memory import inject_memory
    body = {"messages": [{"role": "user", "content": "should I run the linter before committing?"}]}

    result = inject_memory("anthropic", body, project="unknown", db_path=db_path, embedder=e)
    assert "linter" in result["messages"][-1]["content"]


def test_proxy_inject_tool_result_only_message_is_skipped(tmp_path):
    """A user turn carrying only tool results has no query text to recall on."""
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="mem1", kind="memory", project="proj", source="distill",
        text="we use PostgreSQL for the database",
        token_count=12, created_at=100.0, meta={"mem_type": "decision"}
    )
    s.add_artifacts([art], e.embed([art.text]))

    from memor.proxy.memory import inject_memory
    body = {
        "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "1", "content": "exit 0"},
            ]},
        ],
    }
    assert inject_memory("anthropic", body, project="proj", db_path=db_path, embedder=e) == body


def test_proxy_inject_no_memories_returns_unchanged(tmp_path):
    """Proxy should return unchanged body when no memories found."""
    db_path = str(tmp_path / "empty.db")
    e = FakeEmbedder(dim=16)
    SqliteStore(db_path, dim=16)  # Empty DB
    
    from memor.proxy.memory import inject_memory
    body = {
        "model": "claude-sonnet-4-20250514",
        "messages": [
            {"role": "user", "content": "what is the capital of France?"}
        ],
        "max_tokens": 1024
    }
    
    result = inject_memory(
        "anthropic", body,
        project="unknown",
        db_path=db_path,
        embedder=e
    )
    
    # Should return body as-is when no memories
    assert result == body


def test_proxy_inject_with_openai_format(tmp_path):
    """Proxy should work with OpenAI message format too."""
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="mem1", kind="memory", project="proj", source="distill",
        text="we use PostgreSQL for the database",
        token_count=12, created_at=100.0,
        meta={"mem_type": "decision"}
    )
    s.add_artifacts([art], e.embed([art.text]))
    
    from memor.proxy.memory import inject_memory
    body = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "what database do we use?"}
        ]
    }
    
    result = inject_memory(
        "openai", body,
        project="proj",
        db_path=db_path,
        embedder=e
    )
    
    last_msg = result["messages"][-1]
    assert "## Recalled Memories" in last_msg["content"]
    assert "PostgreSQL" in last_msg["content"]
