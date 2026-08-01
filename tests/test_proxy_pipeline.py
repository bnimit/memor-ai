from memor.proxy.pipeline import run_pipeline
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.proxy.adapters import extract_latest_tool_payloads, apply_payload_text, ToolPayload

def test_anthropic_compresses_only_latest_tool_result(tmp_path):
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    old_log = "INFO old\n" * 50
    new_log = "\n".join([f"INFO noise {i}" for i in range(80)] + ["ERROR boom", "Traceback (most recent call last):"])
    body = {
        "model": "claude-sonnet-4-0",
        "messages": [
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": old_log}]},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "2", "content": new_log}]},
        ],
    }
    result = run_pipeline("anthropic", body, store)
    # First tool_result unchanged
    assert result.body["messages"][0]["content"][0]["content"] == old_log
    # Latest compressed
    latest = result.body["messages"][2]["content"][0]["content"]
    assert latest != new_log
    assert "ERROR boom" in latest
    assert result.tokens_after < result.tokens_before
    # Check CCR marker
    assert latest.startswith("[memor:ccr:")
    assert len(result.ccr_ids) == 1
    # Verify stored in CCR
    retrieved = store.ccr_get(result.ccr_ids[0])
    assert retrieved == new_log

def test_openai_compresses_trailing_tool_messages(tmp_path):
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    old_tool = "INFO old\n" * 50
    tool1 = "\n".join([f"INFO noise {i}" for i in range(80)])
    tool2 = "\n".join(["ERROR boom", "Traceback (most recent call last):"] * 10)
    body = {
        "model": "gpt-4",
        "messages": [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": old_tool},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "do more"},
            {"role": "assistant", "tool_calls": [{"id": "2"}, {"id": "3"}]},
            {"role": "tool", "tool_call_id": "2", "content": tool1},
            {"role": "tool", "tool_call_id": "3", "content": tool2},
        ],
    }
    result = run_pipeline("openai", body, store)
    # Old tool message unchanged
    assert result.body["messages"][2]["content"] == old_tool
    # Latest two compressed
    latest1 = result.body["messages"][6]["content"]
    latest2 = result.body["messages"][7]["content"]
    assert latest1 != tool1
    assert latest2 != tool2
    assert latest1.startswith("[memor:ccr:")
    assert latest2.startswith("[memor:ccr:")
    assert len(result.ccr_ids) == 2
    assert result.tokens_after < result.tokens_before

def test_no_tool_payloads_passthrough(tmp_path):
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    body = {
        "model": "claude-sonnet-4-0",
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
    }
    result = run_pipeline("anthropic", body, store)
    assert result.body == body
    assert result.passthrough is True
    assert result.tokens_before == 0
    assert result.tokens_after == 0
    assert len(result.ccr_ids) == 0

def test_extract_anthropic_multiple_tool_results(tmp_path):
    body = {
        "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "1", "content": "result1"},
                {"type": "text", "text": "some text"},
                {"type": "tool_result", "tool_use_id": "2", "content": "result2"},
            ]},
        ],
    }
    payloads = extract_latest_tool_payloads("anthropic", body)
    assert len(payloads) == 2
    assert payloads[0].text == "result1"
    assert payloads[0].path == ["messages", 0, "content", 0, "content"]
    assert payloads[1].text == "result2"
    assert payloads[1].path == ["messages", 0, "content", 2, "content"]

def test_extract_openai_trailing_tools(tmp_path):
    body = {
        "messages": [
            {"role": "tool", "content": "old"},
            {"role": "user", "content": "text"},
            {"role": "tool", "content": "new1"},
            {"role": "tool", "content": "new2"},
        ],
    }
    payloads = extract_latest_tool_payloads("openai", body)
    assert len(payloads) == 2
    assert payloads[0].text == "new1"
    assert payloads[0].path == ["messages", 2, "content"]
    assert payloads[1].text == "new2"
    assert payloads[1].path == ["messages", 3, "content"]

def test_apply_payload_text_immutable():
    original = {
        "messages": [
            {"role": "user", "content": [{"type": "tool_result", "content": "old"}]},
        ],
    }
    path = ["messages", 0, "content", 0, "content"]
    result = apply_payload_text(original, path, "new")
    
    # Original unchanged
    assert original["messages"][0]["content"][0]["content"] == "old"
    # Result updated
    assert result["messages"][0]["content"][0]["content"] == "new"

def test_content_types_tracked(tmp_path):
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    log = "\n".join([f"INFO line {i}" for i in range(50)])
    body = {
        "model": "claude-sonnet-4-0",
        "messages": [
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": log}]},
        ],
    }
    result = run_pipeline("anthropic", body, store)
    assert "log" in result.content_types
    assert result.content_types["log"] == 1
    assert result.passthrough is False
