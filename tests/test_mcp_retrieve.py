import time
import pytest
from memor.store.sqlite_store import SqliteStore


def test_retrieve_hit_and_miss(tmp_path):
    store = SqliteStore(str(tmp_path / "m.db"), dim=16)
    store.ccr_put("abc", "SECRET FULL", "log", created_at=time.time())
    from memor.proxy.mcp_retrieve import retrieve
    assert retrieve("abc", store) == "SECRET FULL"
    assert "CCR miss" in retrieve("nope", store)
