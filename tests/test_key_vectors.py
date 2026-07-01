from memor.store.sqlite_store import SqliteStore

def _tables(s):
    return {r[0] for r in s.db.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()}

def test_key_vector_tables_exist(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    t = _tables(s)
    assert "key_vectors" in t and "vec_keys" in t and "fts_keys" in t

def test_migration_idempotent_on_reopen(tmp_path):
    p = str(tmp_path / "m.db")
    SqliteStore(p, dim=16)
    s2 = SqliteStore(p, dim=16)  # must not raise on second open
    assert "key_vectors" in _tables(s2) and "vec_keys" in _tables(s2) and "fts_keys" in _tables(s2)
