import time

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
        "content_types": {"log": 2},
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
        "content_types": {"search": 1},
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
        "content_types": {"log": 3},
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
    
    # Check content types breakdown (pipeline shape: {content_type: count})
    assert "content_types" in data
    assert isinstance(data["content_types"], list)
    log_entry = next((ct for ct in data["content_types"] if ct["content_type"] == "log"), None)
    assert log_entry is not None
    assert log_entry["count"] == 5
    search_entry = next((ct for ct in data["content_types"] if ct["content_type"] == "search"), None)
    assert search_entry is not None
    assert search_entry["count"] == 1
    # Sorted by count descending
    assert data["content_types"][0]["content_type"] == "log"


def test_savings_ledger_content_types_from_pipeline(tmp_path):
    """content_types written by run_pipeline aggregate correctly in the dashboard."""
    import time
    from fastapi.testclient import TestClient
    from memor.proxy.pipeline import run_pipeline

    db_path = str(tmp_path / "pipe.db")
    s = SqliteStore(db_path, dim=16)

    log = "\n".join(f"INFO line {i}" for i in range(60))
    body = {
        "model": "claude-sonnet-4-0",
        "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "1", "content": log},
            ]},
        ],
    }
    result = run_pipeline("anthropic", body, s)
    assert result.passthrough is False

    s.record_proxy_savings({
        "timestamp": time.time(),
        "agent": "claude",
        "provider": "anthropic",
        "session_id": "s1",
        "tokens_before": result.tokens_before,
        "tokens_after": result.tokens_after,
        "content_types": result.content_types,
        "passthrough": int(result.passthrough),
        "upstream_input_tokens": None,
        "upstream_cache_read_tokens": None,
        "upstream_output_tokens": None,
    })

    from memor.dashboard.server import create_app
    client = TestClient(create_app(db_path))
    data = client.get("/api/savings-ledger?days=30").json()

    assert data["content_types"] == [{"content_type": "log", "count": 1}]
    assert data["summary"]["tokens_before"] == result.tokens_before
    assert data["summary"]["tokens_after"] == result.tokens_after


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


def test_proxy_savings_by_agent_store(tmp_path):
    """get_proxy_savings_by_agent groups by agent; pct excludes passthrough rows."""
    import time
    _, store = _make_app_with_proxy_savings(tmp_path)
    store.record_proxy_savings({
        "timestamp": time.time(),
        "agent": "claude",
        "provider": "anthropic",
        "session_id": "s4",
        "tokens_before": 9999,
        "tokens_after": 9999,
        "content_types": {},
        "passthrough": 1,
    })
    agents = store.get_proxy_savings_by_agent(days=30)
    by_name = {a["agent"]: a for a in agents}

    assert set(by_name) == {"claude", "codex"}

    claude = by_name["claude"]
    assert claude["tokens_before"] == 1500
    assert claude["tokens_after"] == 1200
    assert claude["pct_saved"] == 20.0
    assert claude["requests"] == 2
    assert claude["passthrough_requests"] == 1

    codex = by_name["codex"]
    assert codex["tokens_before"] == 2000
    assert codex["tokens_after"] == 1500
    assert codex["pct_saved"] == 25.0
    assert codex["requests"] == 1
    assert codex["passthrough_requests"] == 0


def test_proxy_savings_by_agent_endpoint(tmp_path):
    """GET /api/proxy-savings-by-agent returns per-agent aggregation."""
    import time
    from fastapi.testclient import TestClient
    app, store = _make_app_with_proxy_savings(tmp_path)
    store.record_proxy_savings({
        "timestamp": time.time(),
        "agent": "claude",
        "provider": "anthropic",
        "session_id": "s4",
        "tokens_before": 9999,
        "tokens_after": 9999,
        "content_types": {},
        "passthrough": 1,
    })
    client = TestClient(app)

    r = client.get("/api/proxy-savings-by-agent?days=30")
    assert r.status_code == 200
    data = r.json()
    assert "agents" in data
    assert len(data["agents"]) == 2

    claude = next(a for a in data["agents"] if a["agent"] == "claude")
    assert claude["tokens_before"] == 1500
    assert claude["pct_saved"] == 20.0
    assert claude["passthrough_requests"] == 1


def test_proxy_savings_by_agent_empty_db(tmp_path):
    """Empty proxy_savings returns empty agents list."""
    from fastapi.testclient import TestClient
    db_path = str(tmp_path / "empty.db")
    SqliteStore(db_path, dim=16)
    from memor.dashboard.server import create_app
    client = TestClient(create_app(db_path))

    r = client.get("/api/proxy-savings-by-agent?days=30")
    assert r.status_code == 200
    assert r.json() == {"agents": []}


def test_compression_endpoint_separates_coverage_from_rate(tmp_path):
    """The dashboard must not quote the blended rate alone.

    The blended figure counts conversation history the compressor is designed
    never to touch, so it understates the compressor and hides that coverage
    is the real constraint.
    """
    from fastapi.testclient import TestClient

    app, _ = _make_app_with_proxy_savings(tmp_path)
    d = TestClient(app).get("/api/compression").json()

    assert "compressible" in d
    # Seeded rows: 3500 -> 2700 across three non-passthrough requests.
    assert d["compressible"]["tokens_before"] == 3500
    assert d["compressible"]["saved"] == 800
    assert d["compressible"]["coverage_pct"] == 100.0


def test_compression_endpoint_reports_cache_when_usage_known(tmp_path):
    from fastapi.testclient import TestClient

    app, _ = _make_app_with_proxy_savings(tmp_path)
    d = TestClient(app).get("/api/compression").json()

    assert d["cache"] is not None
    assert d["cache"]["usage_requests"] == 3
    assert d["cache"]["reads"] == 0
    # Seeded rows report cache_read but never cache_creation.
    assert d["cache"]["writes_observed"] is False


def test_compression_endpoint_reports_null_cache_without_usage(tmp_path):
    """Unmeasured must be distinguishable from measured-as-zero."""
    from fastapi.testclient import TestClient

    from memor.dashboard.server import create_app
    from memor.store.sqlite_store import SqliteStore

    db_path = str(tmp_path / "n.db")
    s = SqliteStore(db_path, dim=16)
    s.record_proxy_savings({
        "timestamp": time.time(), "agent": "claude", "provider": "anthropic",
        "session_id": None, "tokens_before": 100, "tokens_after": 80,
        "content_types": {"log": 1}, "passthrough": 0,
    })
    d = TestClient(create_app(db_path)).get("/api/compression").json()
    assert d["cache"] is None


def test_compression_endpoint_exposes_hook_savings(tmp_path):
    """Hook-path savings must reach the dashboard, not just the CLI report."""
    from fastapi.testclient import TestClient

    from memor.dashboard.server import create_app
    from memor.store.sqlite_store import SqliteStore

    db_path = str(tmp_path / "h.db")
    s = SqliteStore(db_path, dim=16)
    s.record_proxy_savings({
        "timestamp": time.time(), "agent": "claude", "provider": "hook",
        "session_id": "s1", "tokens_before": 1000, "tokens_after": 100,
        "content_types": {"log": 1}, "passthrough": 0,
    })
    d = TestClient(create_app(db_path)).get("/api/compression").json()
    assert d["hook"]["requests"] == 1
    assert d["hook"]["saved_pct"] == 90.0


def test_compression_endpoint_reports_null_hook_without_hook_traffic(tmp_path):
    from fastapi.testclient import TestClient

    app, _ = _make_app_with_proxy_savings(tmp_path)
    assert TestClient(app).get("/api/compression").json()["hook"] is None


def test_compression_endpoint_flags_unreliable_net(tmp_path):
    """Low usage coverage must be visible to the UI, not silently absorbed."""
    from fastapi.testclient import TestClient

    from memor.dashboard.server import create_app
    from memor.store.sqlite_store import SqliteStore

    db_path = str(tmp_path / "u.db")
    s = SqliteStore(db_path, dim=16)
    s.record_proxy_savings({
        "timestamp": time.time(), "agent": "claude", "provider": "anthropic",
        "session_id": None, "tokens_before": 1000, "tokens_after": 500,
        "content_types": {"log": 1}, "passthrough": 0,
        "upstream_input_tokens": 10, "upstream_cache_read_tokens": 100,
        "upstream_cache_creation_tokens": 5,
    })
    for _ in range(9):
        s.record_proxy_savings({
            "timestamp": time.time(), "agent": "claude", "provider": "anthropic",
            "session_id": None, "tokens_before": 1000, "tokens_after": 500,
            "content_types": {"log": 1}, "passthrough": 0,
        })
    cache = TestClient(create_app(db_path)).get("/api/compression").json()["cache"]
    assert cache["net_is_reliable"] is False
    assert round(cache["usage_coverage_pct"]) == 10
