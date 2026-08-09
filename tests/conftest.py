"""Shared pytest fixtures."""
import os
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_memor_config(tmp_path, monkeypatch):
    """Keep tests off the developer's ~/.memor config (proxy flags, upstreams)."""
    import memor.config as cfg

    state_dir = tmp_path / "memor-state"
    state_dir.mkdir()
    monkeypatch.setattr(cfg, "STATE_DIR", state_dir)
    monkeypatch.setattr(cfg, "CONFIG_PATH", state_dir / "config.json")


def _real_ledger_rows() -> int | None:
    """Row count of the developer's own savings ledger, or None if absent."""
    db = Path(os.path.expanduser("~/.memor/memor.db"))
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM proxy_savings").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


@pytest.fixture(autouse=True)
def ledger_is_not_polluted():
    """Fail any test that appends rows to the real ~/.memor ledger.

    Config was already isolated, but the savings ledger was not, and the gap
    was not theoretical: the PostToolUse hook derives its database path from
    ``Path.home()``, which a subprocess resolves for real no matter what the
    parent monkeypatched. That wrote 35 fixture rows into a live database and
    they were then read back off the dashboard as genuine traffic.

    Monkeypatching cannot prevent this from a parent process, so the next best
    thing is to make it impossible to do quietly.
    """
    before = _real_ledger_rows()
    yield
    if before is None:
        return
    after = _real_ledger_rows()
    if after is not None and after > before:
        pytest.fail(
            f"test wrote {after - before} row(s) to the real ~/.memor ledger; "
            "point HOME at a temporary directory for subprocesses, or pass an "
            "explicit db path"
        )
