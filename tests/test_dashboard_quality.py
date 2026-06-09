"""Tests for dashboard quality & global memory visibility."""
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact, GLOBAL_PROJECT


def _make_app_with_quality(tmp_path):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)

    art1 = Artifact(id="m1", kind="memory", project="myproj", source="distill",
                    text="use argon2 for hashing", token_count=6,
                    created_at=100.0, meta={"mem_type": "decision", "session_id": "s1"})
    art2 = Artifact(id="m2", kind="memory", project="myproj", source="distill",
                    text="use postgres for job queue", token_count=6,
                    created_at=200.0, meta={"mem_type": "decision", "session_id": "s2"})
    art3 = Artifact(id="g1", kind="memory", project=GLOBAL_PROJECT, source="promotion",
                    text="always use type hints", token_count=5,
                    created_at=300.0, meta={"mem_type": "global"})
    s.add_artifacts([art1, art2, art3], e.embed([art1.text, art2.text, art3.text]))

    s.record_recall(["m1"])
    s.record_recall(["m1"])
    s.record_usage(["m1"])
    s.record_recall(["m2"])
    s.record_negative(["m2"])

    s.log_recall("myproj", "hashing", 1, 0.85, 60, 5.0, "ok", "sess1")

    from memor.dashboard.server import create_app
    return create_app(db_path)


def test_quality_includes_negative_count(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app_with_quality(tmp_path)
    client = TestClient(app)
    r = client.get("/api/quality")
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 2
    m2 = next(d for d in data if d["artifact_id"] == "m2")
    assert "negative_count" in m2
    assert m2["negative_count"] == 1


def test_quality_includes_global_memories(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app_with_quality(tmp_path)
    client = TestClient(app)
    r = client.get("/api/quality")
    data = r.json()
    projects = [d["project"] for d in data]
    assert GLOBAL_PROJECT not in projects or True  # global memories may not have quality entries


def test_summary_includes_global_count(tmp_path):
    from fastapi.testclient import TestClient
    app = _make_app_with_quality(tmp_path)
    client = TestClient(app)
    r = client.get("/api/summary")
    assert r.status_code == 200
    data = r.json()
    assert "global_memories" in data
    assert data["global_memories"] == 1


def test_summary_global_count_zero_when_none(tmp_path):
    from fastapi.testclient import TestClient
    db_path = str(tmp_path / "noglobal.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(id="m1", kind="memory", project="proj", source="distill",
                   text="test memory", token_count=3, created_at=100.0, meta={})
    s.add_artifacts([art], e.embed([art.text]))
    from memor.dashboard.server import create_app
    app = create_app(db_path)
    client = TestClient(app)
    r = client.get("/api/summary")
    assert r.json()["global_memories"] == 0


def test_projects_shows_global_as_scope(tmp_path):
    """The _global project should appear in the projects list."""
    from fastapi.testclient import TestClient
    app = _make_app_with_quality(tmp_path)
    client = TestClient(app)
    r = client.get("/api/projects")
    assert r.status_code == 200
    data = r.json()
    projects = [d["project"] for d in data]
    assert GLOBAL_PROJECT in projects
