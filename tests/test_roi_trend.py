"""Tests for daily ROI trend — tool call reduction over time."""
from memor.store.sqlite_store import SqliteStore
from memor.turn_metrics import TurnMetric


def test_roi_trend_groups_by_day(tmp_path):
    db_path = str(tmp_path / "m.db")
    store = SqliteStore(db_path, dim=16)

    day1_base = 1749427200.0  # 2025-06-09 00:00:00 UTC
    day2_base = day1_base + 86400

    store.save_turn_metrics("s1", "p", [
        TurnMetric(turn_idx=0, user_timestamp=day1_base + 100, tool_call_count=1, had_recall=True),
        TurnMetric(turn_idx=1, user_timestamp=day1_base + 200, tool_call_count=4, had_recall=False),
    ])
    store.save_turn_metrics("s2", "p", [
        TurnMetric(turn_idx=0, user_timestamp=day2_base + 100, tool_call_count=2, had_recall=True),
        TurnMetric(turn_idx=1, user_timestamp=day2_base + 200, tool_call_count=6, had_recall=False),
    ])

    trend = store.get_roi_trend()
    assert len(trend) == 2
    for day in trend:
        assert "day" in day
        assert "avg_with" in day
        assert "avg_without" in day
        assert "reduction_pct" in day
        assert "turns_total" in day


def test_roi_trend_calculates_reduction(tmp_path):
    db_path = str(tmp_path / "m.db")
    store = SqliteStore(db_path, dim=16)

    base = 1749427200.0
    store.save_turn_metrics("s1", "p", [
        TurnMetric(turn_idx=0, user_timestamp=base + 100, tool_call_count=2, had_recall=True),
        TurnMetric(turn_idx=1, user_timestamp=base + 200, tool_call_count=2, had_recall=True),
        TurnMetric(turn_idx=2, user_timestamp=base + 300, tool_call_count=4, had_recall=False),
        TurnMetric(turn_idx=3, user_timestamp=base + 400, tool_call_count=4, had_recall=False),
    ])

    trend = store.get_roi_trend()
    assert len(trend) == 1
    day = trend[0]
    assert day["avg_with"] == 2.0
    assert day["avg_without"] == 4.0
    assert day["reduction_pct"] == 50.0


def test_roi_trend_filters_by_project(tmp_path):
    db_path = str(tmp_path / "m.db")
    store = SqliteStore(db_path, dim=16)

    base = 1749427200.0
    store.save_turn_metrics("s1", "proj-a", [
        TurnMetric(turn_idx=0, user_timestamp=base + 100, tool_call_count=1, had_recall=True),
        TurnMetric(turn_idx=1, user_timestamp=base + 200, tool_call_count=5, had_recall=False),
    ])
    store.save_turn_metrics("s2", "proj-b", [
        TurnMetric(turn_idx=0, user_timestamp=base + 100, tool_call_count=3, had_recall=True),
        TurnMetric(turn_idx=1, user_timestamp=base + 200, tool_call_count=3, had_recall=False),
    ])

    trend_a = store.get_roi_trend(project="proj-a")
    assert len(trend_a) == 1
    assert trend_a[0]["avg_with"] == 1.0

    trend_b = store.get_roi_trend(project="proj-b")
    assert len(trend_b) == 1
    assert trend_b[0]["avg_with"] == 3.0


def test_roi_trend_skips_days_without_both_types(tmp_path):
    """Days that have only recall or only non-recall turns can't compute reduction."""
    db_path = str(tmp_path / "m.db")
    store = SqliteStore(db_path, dim=16)

    base = 1749427200.0
    store.save_turn_metrics("s1", "p", [
        TurnMetric(turn_idx=0, user_timestamp=base + 100, tool_call_count=2, had_recall=True),
        TurnMetric(turn_idx=1, user_timestamp=base + 200, tool_call_count=3, had_recall=True),
    ])

    trend = store.get_roi_trend()
    assert len(trend) == 0
