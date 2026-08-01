"""read_dim must not create an empty database when the path is missing."""
from pathlib import Path

from memor.store.sqlite_store import SqliteStore, read_dim


def test_read_dim_missing_db_does_not_create_file(tmp_path):
    missing = tmp_path / "nope" / "memor.db"
    assert not missing.exists()
    assert read_dim(str(missing), 256) == 256
    assert not missing.exists()
    assert not missing.parent.exists()


def test_read_dim_reads_existing_db(tmp_path):
    db = tmp_path / "m.db"
    SqliteStore(str(db), dim=128)
    assert read_dim(str(db), 256) == 128
