import time
from pathlib import Path
import pytest
from memor.store.sqlite_store import SqliteStore


def test_retrieve_hit_and_miss(tmp_path):
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    store.ccr_put("abc", "SECRET FULL", "log", created_at=time.time())
    from memor.proxy.mcp_retrieve import retrieve
    assert retrieve("abc", store) == "SECRET FULL"
    assert "CCR miss" in retrieve("nope", store)


def test_default_db_path_is_the_shared_memor_db(monkeypatch, tmp_path):
    from memor.proxy import mcp_retrieve

    monkeypatch.delenv("MEMOR_DB", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert mcp_retrieve.default_db_path() == str(tmp_path / ".memor" / "memor.db")


def test_open_store_uses_dim_recorded_in_db(tmp_path, monkeypatch):
    """The MCP server must not assume 384; it attaches to whatever exists."""
    from memor.proxy import mcp_retrieve

    db_path = str(tmp_path / "m.db")
    seeded = SqliteStore(db_path, dim=16)
    seeded.ccr_put("xyz", "ORIGINAL", "log", created_at=time.time())

    monkeypatch.setenv("MEMOR_DB", db_path)
    store = mcp_retrieve.open_store()
    assert store.dim == 16
    assert mcp_retrieve.retrieve("xyz", store) == "ORIGINAL"
