from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


def _make_app(tmp_path):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(id="a1", kind="memory", project="myproj", source="distill",
                   text="use argon2 for hashing", token_count=6,
                   created_at=100.0, meta={"mem_type": "decision", "session_id": "s1"})
    s.add_artifacts([art], e.embed([art.text]))
    s.log_recall("myproj", "password hashing", 2, 0.85, 120, 45.0, "ok", "sess1")
    s.log_recall("myproj", "auth bug", 0, 0.0, 0, 12.0, "no_hits", "sess2")

    from memor.dashboard.server import create_app
    app = create_app(db_path)
    return app


def test_summary_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["total_recalls"] == 2
    assert data["total_tokens"] == 120
    assert data["hit_rate"] == 0.5


def test_projects_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/projects")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["project"] == "myproj"


def test_recalls_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/recalls?limit=10")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2


def test_recalls_filter_by_project(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/recalls?project=nonexistent")
    assert r.status_code == 200
    assert len(r.json()) == 0


def test_health_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert "onboarding_status" in data
    assert "artifact_counts" in data


def test_savings_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/savings")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_empty_db(tmp_path):
    from fastapi.testclient import TestClient
    db_path = str(tmp_path / "empty.db")
    SqliteStore(db_path, dim=16)
    from memor.dashboard.server import create_app
    app = create_app(db_path)
    client = TestClient(app)
    r = client.get("/api/summary")
    assert r.status_code == 200
    assert r.json()["total_recalls"] == 0


def test_index_html_served(tmp_path):
    from fastapi.testclient import TestClient
    db_path = str(tmp_path / "m.db")
    SqliteStore(db_path, dim=16)
    from memor.dashboard.server import create_app
    app = create_app(db_path)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
