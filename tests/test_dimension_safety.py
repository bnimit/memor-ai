import pytest
from memor.store.sqlite_store import SqliteStore


def test_meta_table_stores_dim(tmp_path):
    db = str(tmp_path / "m.db")
    s = SqliteStore(db, dim=16)
    row = s.db.execute("SELECT value FROM meta WHERE key='dim'").fetchone()
    assert row is not None
    assert row["value"] == "16"


def test_dimension_mismatch_raises(tmp_path):
    db = str(tmp_path / "m.db")
    SqliteStore(db, dim=16)
    with pytest.raises(SystemExit, match="dim=16.*dim=384"):
        SqliteStore(db, dim=384)


def test_same_dimension_reopens_fine(tmp_path):
    db = str(tmp_path / "m.db")
    SqliteStore(db, dim=16)
    s2 = SqliteStore(db, dim=16)
    assert s2.dim == 16


def test_wal_mode_enabled(tmp_path):
    db = str(tmp_path / "m.db")
    s = SqliteStore(db, dim=16)
    mode = s.db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_recall_log_table_exists(tmp_path):
    db = str(tmp_path / "m.db")
    s = SqliteStore(db, dim=16)
    s.db.execute("SELECT COUNT(*) FROM recall_log").fetchone()
