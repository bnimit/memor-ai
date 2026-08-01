from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


def _make_app_with_proxy_savings(tmp_path):
    """App with seeded proxy_savings data."""
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    
    # Seed some proxy savings
    import time
    now = time.time()
    s.record_proxy_savings({
        "timestamp": now - 86400,
        "agent": "claude",
        "provider": "anthropic",
        "session_id": "s1",
        "tokens_before": 1000,
        "tokens_after": 800,
        "content_types": {"text/code": {"before": 1000, "after": 800}},
        "passthrough": 0,
        "upstream_input_tokens": 800,
        "upstream_cache_read_tokens": 0,
        "upstream_output_tokens": 100,
    })
    s.record_proxy_savings({
        "timestamp": now - 172800,
        "agent": "claude",
        "provider": "anthropic",
        "session_id": "s2",
        "tokens_before": 500,
        "tokens_after": 400,
        "content_types": {"text/markdown": {"before": 500, "after": 400}},
        "passthrough": 0,
        "upstream_input_tokens": 400,
        "upstream_cache_read_tokens": 0,
        "upstream_output_tokens": 50,
    })
    s.record_proxy_savings({
        "timestamp": now - 259200,
        "agent": "codex",
        "provider": "openai",
        "session_id": "s3",
        "tokens_before": 2000,
        "tokens_after": 1500,
        "content_types": {"text/code": {"before": 2000, "after": 1500}},
        "passthrough": 0,
        "upstream_input_tokens": 1500,
        "upstream_cache_read_tokens": 0,
        "upstream_output_tokens": 200,
    })
    
    from memor.dashboard.server import create_app
    app = create_app(db_path)
    return app, s


def test_savings_ledger_endpoint(tmp_path):
    """Test GET /api/savings-ledger returns summary + daily series + content types."""
    from fastapi.testclient import TestClient
    app, _ = _make_app_with_proxy_savings(tmp_path)
    client = TestClient(app)
    
    r = client.get("/api/savings-ledger?days=30")
    assert r.status_code == 200
    data = r.json()
    
    # Check summary structure
    assert "summary" in data
    assert data["summary"]["tokens_before"] == 3500
    assert data["summary"]["tokens_after"] == 2700
    assert data["summary"]["pct_saved"] > 0
    
    # Check daily series
    assert "per_day" in data
    assert isinstance(data["per_day"], list)
    assert len(data["per_day"]) >= 3
    
    # Check content types breakdown
    assert "content_types" in data
    assert isinstance(data["content_types"], list)
    code_entry = next((ct for ct in data["content_types"] if ct["content_type"] == "text/code"), None)
    assert code_entry is not None
    assert code_entry["tokens_before"] == 3000
    assert code_entry["tokens_after"] == 2300


def test_proxy_status_endpoint(tmp_path):
    """Test GET /api/proxy-status returns proxy, hook, daemon status + proxy_agents."""
    from fastapi.testclient import TestClient
    app, _ = _make_app_with_proxy_savings(tmp_path)
    client = TestClient(app)
    
    r = client.get("/api/proxy-status")
    assert r.status_code == 200
    data = r.json()
    
    # Check structure
    assert "proxy" in data
    assert "hook" in data
    assert "daemon" in data
    assert "proxy_agents" in data
    
    # Values should be booleans
    assert isinstance(data["proxy"], bool)
    assert isinstance(data["hook"], bool)
    assert isinstance(data["daemon"], bool)
    
    # proxy_agents should be a dict
    assert isinstance(data["proxy_agents"], dict)


def test_savings_ledger_empty_db(tmp_path):
    """Test /api/savings-ledger with empty proxy_savings table."""
    from fastapi.testclient import TestClient
    db_path = str(tmp_path / "empty.db")
    SqliteStore(db_path, dim=16)
    from memor.dashboard.server import create_app
    app = create_app(db_path)
    client = TestClient(app)
    
    r = client.get("/api/savings-ledger?days=30")
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["tokens_before"] == 0
    assert data["summary"]["tokens_after"] == 0
    assert data["summary"]["pct_saved"] == 0
    assert data["per_day"] == []
    assert data["content_types"] == []
