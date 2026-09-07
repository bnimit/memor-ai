"""Tests for per-agent liveness and evidence freshness."""
from __future__ import annotations

import time

from memor.liveness import (
    BEHAVIOUR_CHANGES,
    FRESH_DAYS,
    MIN_SAMPLE,
    STALE_DAYS,
    agent_liveness,
    liveness_summary,
)
from memor.store.sqlite_store import SqliteStore

_DAY = 86_400
_CUTOFF = BEHAVIOUR_CHANGES[-1][1]


def _store(tmp_path) -> SqliteStore:
    return SqliteStore(str(tmp_path / "m.db"), dim=8)


def _recall(store: SqliteStore, *, agent: str, at: float, hits: int) -> None:
    store.db.execute(
        "INSERT INTO recall_log(timestamp, project, query_preview, hits_count,"
        " top_score, tokens_injected, latency_ms, status, session_id, agent)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (at, "demo", "q", hits, 0.5 if hits else 0.0, 10, 1.0,
         "ok" if hits else "no_hits", "s1", agent),
    )
    store.db.commit()


def test_behaviour_change_epoch_matches_its_date() -> None:
    """A hand-computed epoch here was two days out; keep it derived."""
    from datetime import datetime, timezone

    for _label, epoch, day in BEHAVIOUR_CHANGES:
        expected = datetime.strptime(day, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        ).timestamp()
        assert epoch == expected


def test_agent_reading_today_is_live(tmp_path) -> None:
    store = _store(tmp_path)
    now = time.time()
    for _ in range(MIN_SAMPLE):
        _recall(store, agent="claude", at=now - _DAY, hits=1)

    (entry,) = agent_liveness(store, now=now)

    assert entry.status == "live"
    assert entry.hit_rate == 1.0


def test_agent_that_stopped_reading_is_stale(tmp_path) -> None:
    """The failure this module exists for: silence nobody noticed."""
    store = _store(tmp_path)
    now = time.time()
    for _ in range(MIN_SAMPLE):
        _recall(store, agent="cursor", at=now - (STALE_DAYS + 5) * _DAY, hits=1)

    (entry,) = agent_liveness(store, now=now)

    assert entry.status == "stale"
    assert any("check the integration" in n for n in entry.notes)


def test_recent_silence_is_quiet_not_stale(tmp_path) -> None:
    """A tool you did not use this week is not a broken tool."""
    store = _store(tmp_path)
    now = time.time()
    _recall(store, agent="jcode", at=now - (FRESH_DAYS + 2) * _DAY, hits=1)

    (entry,) = agent_liveness(store, now=now)

    assert entry.status == "quiet"


def test_recalls_predating_a_fix_are_excluded_from_the_hit_rate(tmp_path) -> None:
    """The misreading this prevents: 38 pre-fix zero-hit rows read as current.

    A lifetime total would report 0%, which described code that had already
    been replaced. Only recalls generated after the fix may be judged.
    """
    store = _store(tmp_path)
    now = time.time()
    for _ in range(38):
        _recall(store, agent="codex", at=_CUTOFF - 30 * _DAY, hits=0)
    for _ in range(MIN_SAMPLE):
        _recall(store, agent="codex", at=now - _DAY, hits=1)

    (entry,) = agent_liveness(store, now=now)

    assert entry.recalls == 38 + MIN_SAMPLE
    assert entry.stale_recalls == 38
    # Not 0.0, which is what the lifetime total would have said.
    assert entry.hit_rate == 1.0


def test_a_tiny_sample_refuses_to_report_a_hit_rate(tmp_path) -> None:
    """Better to say "too few to judge" than to publish a verdict from 2 rows."""
    store = _store(tmp_path)
    now = time.time()
    _recall(store, agent="codex", at=now - _DAY, hits=0)

    (entry,) = agent_liveness(store, now=now)

    assert entry.hit_rate is None
    assert any("too few to judge" in n for n in entry.notes)


def test_all_evidence_predating_a_fix_is_called_out(tmp_path) -> None:
    store = _store(tmp_path)
    now = time.time()
    for _ in range(38):
        _recall(store, agent="codex", at=_CUTOFF - 30 * _DAY, hits=0)

    (entry,) = agent_liveness(store, now=now)

    assert entry.recent_recalls == 0
    assert entry.hit_rate is None
    assert any("no longer runs" in n for n in entry.notes)


def test_agents_are_ordered_by_most_recent_reader(tmp_path) -> None:
    store = _store(tmp_path)
    now = time.time()
    _recall(store, agent="old", at=now - 40 * _DAY, hits=1)
    _recall(store, agent="new", at=now - _DAY, hits=1)

    assert [e.agent for e in agent_liveness(store, now=now)] == ["new", "old"]


def test_summary_counts_live_stale_and_judgeable(tmp_path) -> None:
    store = _store(tmp_path)
    now = time.time()
    for _ in range(MIN_SAMPLE):
        _recall(store, agent="claude", at=now - _DAY, hits=1)
    _recall(store, agent="cursor", at=now - (STALE_DAYS + 5) * _DAY, hits=0)

    summary = liveness_summary(store, now=now)

    assert summary["live"] == 1
    assert summary["stale"] == 1
    # Cursor has one recall, far below the sample floor.
    assert summary["judgeable"] == 1


def test_empty_store_reports_nothing_rather_than_failing(tmp_path) -> None:
    store = _store(tmp_path)

    assert agent_liveness(store) == []
    assert liveness_summary(store)["live"] == 0


def test_doctor_names_the_agent_that_stopped_reading(tmp_path) -> None:
    """The signal has to reach a human, or it repeats the original failure."""
    from typer.testing import CliRunner

    from memor.cli import app

    store = _store(tmp_path)
    now = time.time()
    for _ in range(MIN_SAMPLE):
        _recall(store, agent="claude", at=now - _DAY, hits=1)
    _recall(store, agent="cursor", at=now - (STALE_DAYS + 5) * _DAY, hits=0)

    result = CliRunner().invoke(app, ["doctor", "--db", str(tmp_path / "m.db")])

    assert result.exit_code == 0
    assert "cursor" in result.stdout
    assert "Stopped reading" in result.stdout


def test_doctor_on_a_store_nobody_reads_says_so(tmp_path) -> None:
    from typer.testing import CliRunner

    from memor.cli import app

    _store(tmp_path)

    result = CliRunner().invoke(app, ["doctor", "--db", str(tmp_path / "m.db")])

    assert result.exit_code == 0
    assert "No agent has ever read from this store." in result.stdout
    assert "install-hook" in result.stdout
