import time
from memor.store.sqlite_store import SqliteStore

def test_record_and_summary(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.record_proxy_savings({
        "timestamp": time.time(),
        "agent": "claude",
        "provider": "anthropic",
        "session_id": "s1",
        "tokens_before": 1000,
        "tokens_after": 400,
        "content_types": {"log": 1},
        "passthrough": 0,
    })
    summary = s.get_proxy_savings_summary(days=30)
    assert summary["tokens_before"] == 1000
    assert summary["tokens_after"] == 400
    assert summary["pct_saved"] == 60.0

def test_ccr_put_get_evict(tmp_path):
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)
    s.ccr_put("b1", "FULL TEXT", "log", created_at=1.0)
    assert s.ccr_get("b1") == "FULL TEXT"
    n = s.ccr_evict(ttl_seconds=0, max_bytes=1)  # everything expired / over cap
    assert n >= 1
    assert s.ccr_get("b1") is None
