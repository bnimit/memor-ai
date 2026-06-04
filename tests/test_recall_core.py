from memor.recall import recall, RecallResult
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


def _seed_store(tmp_path, project="testproj"):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    arts = [
        Artifact(id="a1", kind="memory", project=project, source="distill",
                 text="we decided to use argon2 for password hashing in the auth module",
                 token_count=12, created_at=100.0,
                 meta={"mem_type": "decision", "session_id": "s1"}),
        Artifact(id="a2", kind="memory", project=project, source="distill",
                 text="the root cause of the login bug was a race condition in token refresh",
                 token_count=14, created_at=200.0,
                 meta={"mem_type": "bugfix", "session_id": "s2"}),
    ]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    return s, e


def test_recall_returns_hits(tmp_path):
    s, e = _seed_store(tmp_path)
    result = recall("password hashing", "testproj", str(tmp_path / "m.db"),
                    embedder=e, k=8, threshold=0.0)
    assert isinstance(result, RecallResult)
    assert result.hits_count > 0
    assert result.status == "ok"
    assert result.tokens_injected > 0


def test_recall_threshold_filters_low_scores(tmp_path):
    s, e = _seed_store(tmp_path)
    result = recall("completely unrelated quantum physics topic", "testproj",
                    str(tmp_path / "m.db"), embedder=e, k=8, threshold=0.99)
    assert result.status == "no_hits"
    assert result.hits_count == 0


def test_recall_empty_project(tmp_path):
    e = FakeEmbedder(dim=16)
    SqliteStore(str(tmp_path / "m.db"), dim=16)
    result = recall("anything", "nonexistent", str(tmp_path / "m.db"),
                    embedder=e, k=8, threshold=0.0)
    assert result.status == "no_hits"


def test_recall_no_db(tmp_path):
    e = FakeEmbedder(dim=16)
    result = recall("anything", "proj", str(tmp_path / "nope.db"), embedder=e)
    assert result.status == "empty_db"


def test_recall_extractive_only_status(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    art = Artifact(id="m1", kind="memory", project="p", source="distill",
                   text="extracted chunk about authentication patterns and security",
                   token_count=10, created_at=100.0,
                   meta={"mem_type": "extract", "session_id": "s1"})
    s.add_artifacts([art], e.embed([art.text]))
    result = recall("authentication", "p", str(tmp_path / "m.db"),
                    embedder=e, k=8, threshold=0.0)
    assert result.status == "extractive_only"


def test_recall_result_has_formatted_context(tmp_path):
    s, e = _seed_store(tmp_path)
    result = recall("password hashing", "testproj", str(tmp_path / "m.db"),
                    embedder=e, k=8, threshold=0.0)
    assert "## Recalled Memories" in result.formatted_context
    assert "Memor:" in result.status_message


def test_recall_filters_current_session(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    arts = [
        Artifact(id="a1", kind="memory", project="p", source="distill",
                 text="auth pattern from old session about login security",
                 token_count=10, created_at=100.0,
                 meta={"mem_type": "decision", "session_id": "old-sess"}),
        Artifact(id="a2", kind="memory", project="p", source="distill",
                 text="auth pattern from current session about login security",
                 token_count=10, created_at=200.0,
                 meta={"mem_type": "decision", "session_id": "current-sess"}),
    ]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    result = recall("auth login", "p", str(tmp_path / "m.db"),
                    embedder=e, k=8, threshold=0.0, session_id="current-sess")
    ids = [line for line in result.formatted_context.split("\n") if "session" in line.lower()]
    assert "current-sess" not in " ".join(ids) or result.hits_count == 1
