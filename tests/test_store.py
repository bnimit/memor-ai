from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact, Scope
from memor.embed.fake import FakeEmbedder

def make(id, project, text, created, kind="session_chunk"):
    return Artifact(id=id, kind=kind, project=project, source="t",
                    text=text, token_count=len(text.split()), created_at=created, meta={})

def test_add_search_scope_and_edges(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    arts = [make("a1","stablex","auth refresh token loop",100),
            make("a2","stablex","emscripten sync bug",90),
            make("a3","other","auth refresh token loop",100)]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))

    q = e.embed(["auth refresh"])[0]
    hits = s.search(q, Scope(project="stablex"), k=5)
    ids = [a.id for a, _ in hits]
    assert "a1" in ids and "a3" not in ids       # scope filter applied
    assert hits[0][0].id == "a1"                  # most similar first

    s.add_edge("a1", "a2", "fixes")
    nbrs = [a.id for a in s.neighbors(["a1"], ["fixes"], hops=1)]
    assert nbrs == ["a2"]

def test_deactivate_excludes_from_search(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.add_artifacts([make("old","p","use library X",10)], e.embed(["use library X"]))
    s.add_artifacts([make("new","p","use library Y instead",20)], e.embed(["use library Y instead"]))
    s.deactivate("old", superseded_by="new")
    ids = [a.id for a, _ in s.search(e.embed(["use library"])[0], Scope(project="p"), k=5)]
    assert "old" not in ids and "new" in ids


# --- KNN-fetch cap + batched quality lookup (safe retrieval wins) ---

def test_search_knn_fetch_capped(tmp_path):
    # A large k must not exceed sqlite-vec's internal knn limit (4096).
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.add_artifacts([make("a", "p", "hello world", 1)], e.embed(["hello world"]))
    hits = s.search(e.embed(["hello world"])[0], Scope(project="p"), k=300)
    assert len(hits) <= 1  # no OperationalError, returns what's available


def test_get_quality_scores_batch_matches_per_id(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.record_recall(["a", "b"])
    s.record_usage(["a"])
    scores = s.get_quality_scores(["a", "b", "missing"])
    assert scores["a"] == s.get_quality_score("a")
    assert scores["b"] == s.get_quality_score("b")
    assert "missing" not in scores
    assert s.get_quality_scores([]) == {}


# --- reaffirmation (temporal-validity recency) ---

def test_migrate_last_reaffirmed_column(tmp_path):
    import sqlite3
    db_path = str(tmp_path / "m.db")
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE artifacts(id TEXT PRIMARY KEY, kind TEXT, project TEXT, "
               "source TEXT, text TEXT, token_count INTEGER, created_at REAL, meta TEXT, "
               "active INTEGER DEFAULT 1, superseded_by TEXT)")
    db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta(key,value) VALUES('dim','16')")
    db.commit(); db.close()
    s = SqliteStore(db_path, dim=16)
    cols = [r[1] for r in s.db.execute("PRAGMA table_info(artifacts)").fetchall()]
    assert "last_reaffirmed" in cols


def test_reaffirm_sets_max_never_backward(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.add_artifacts([make("m1", "p", "use argon2", 100, kind="memory")], e.embed(["use argon2"]))
    s.reaffirm(["m1"], 500.0)
    assert s.get_reaffirmed_timestamps(["m1"])["m1"] == 500.0
    s.reaffirm(["m1"], 300.0)            # older — must not move backward
    assert s.get_reaffirmed_timestamps(["m1"])["m1"] == 500.0
    s.reaffirm(["m1"], 900.0)            # newer — advances
    assert s.get_reaffirmed_timestamps(["m1"])["m1"] == 900.0


def test_get_reaffirmed_timestamps_batch(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.add_artifacts([make("m1", "p", "a", 1, kind="memory"),
                     make("m2", "p", "b", 1, kind="memory")], e.embed(["a", "b"]))
    s.reaffirm(["m1"], 700.0)
    got = s.get_reaffirmed_timestamps(["m1", "m2", "missing"])
    assert got["m1"] == 700.0
    assert "m2" not in got           # never reaffirmed -> absent (caller defaults to 0)
    assert "missing" not in got
    assert s.get_reaffirmed_timestamps([]) == {}
