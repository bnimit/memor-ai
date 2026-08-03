"""Agent desk panes + filtered savings series."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from memor.embed.fake import FakeEmbedder
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact


def _seed(tmp_path):
    db_path = str(tmp_path / "desk.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(
        id="a1",
        kind="memory",
        project="p1",
        source="distill",
        text="use bcrypt",
        token_count=4,
        created_at=100.0,
        meta={"mem_type": "decision"},
    )
    s.add_artifacts([art], e.embed([art.text]))
    s.log_recall("p1", "hashing", 2, 0.9, 80, 20.0, "ok", "s1", agent="claude")
    s.log_recall("p1", "cursor q", 1, 0.7, 40, 18.0, "ok", "s2", agent="cursor")
    s.log_recall("p1", "miss", 0, 0.0, 0, 10.0, "no_hits", "s3", agent="cursor")
    now = time.time()
    s.record_proxy_savings({
        "timestamp": now - 86400,
        "agent": "cursor",
        "provider": "openai",
        "session_id": "w1",
        "tokens_before": 1000,
        "tokens_after": 600,
        "content_types": {"text": 1},
        "passthrough": 0,
    })
    s.record_proxy_savings({
        "timestamp": now,
        "agent": "cursor",
        "provider": "openai",
        "session_id": "w2",
        "tokens_before": 500,
        "tokens_after": 400,
        "content_types": {"log": 1},
        "passthrough": 0,
    })
    from memor.dashboard.server import create_app
    return create_app(db_path), s


def test_agent_desk_endpoint(tmp_path):
    app, _ = _seed(tmp_path)
    client = TestClient(app)
    r = client.get("/api/agent-desk?agent=cursor")
    assert r.status_code == 200
    data = r.json()
    assert data["stats"]["agent"] == "cursor"
    assert data["stats"]["recalls"] == 2
    assert data["stats"]["hits"] == 1
    assert data["stats"]["proxy"]["tokens_before"] == 1500
    assert data["stats"]["proxy"]["pct_saved"] > 0
    assert len(data["recalls"]) == 2
    assert data["savings_series"][-1]["cumulative_saved"] == 500  # 400 + 100


def test_recall_trend_filter_by_agent(tmp_path):
    app, _ = _seed(tmp_path)
    client = TestClient(app)
    r = client.get("/api/recall-trend?days=30&agent=cursor")
    assert r.status_code == 200
    rows = r.json()
    assert sum(row["recalls"] for row in rows) == 2


def test_savings_ledger_includes_cumulative(tmp_path):
    app, _ = _seed(tmp_path)
    client = TestClient(app)
    r = client.get("/api/savings-ledger?days=30&agent=cursor")
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["tokens_before"] == 1500
    assert data["per_day"][-1]["cumulative_saved"] == 500


def test_dashboard_html_has_desk_tabs(tmp_path):
    app, _ = _seed(tmp_path)
    client = TestClient(app)
    html = client.get("/").text
    assert "desk-tabs" in html
    assert "pane-overview" in html
    assert "pane-agent" in html
    assert "Cumulative tokens saved" in html
    assert "badge-cursor" in html
    # The Cursor wire MITM was removed — no chip, colour, or label may survive.
    assert "cursor-wire" not in html
    assert "cursor_wire" not in html


def test_recall_worth_endpoint(tmp_path):
    """The episode meter must never take the dashboard down, even with no data."""
    app, _ = _seed(tmp_path)
    client = TestClient(app)
    r = client.get("/api/recall-worth")
    assert r.status_code == 200
    data = r.json()
    assert "overall" in data
    assert data["overall"]["verdict"] in {
        "insufficient_data", "no_effect", "saves", "costs",
    }


def test_dashboard_html_has_recall_worth_panel(tmp_path):
    app, _ = _seed(tmp_path)
    html = TestClient(app).get("/").text
    assert "recall-worth-panel" in html
    assert "Does recall reduce work?" in html
    # A null must be presentable, not hidden.
    assert "no measurable effect" in html
