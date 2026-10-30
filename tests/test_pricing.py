"""Per-model pricing of saved tokens, and the dashboard endpoint built on it."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from memor.pricing import normalize_model, price_for


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path, monkeypatch):
    import memor.config as cfg

    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "STATE_DIR", tmp_path)


@pytest.mark.parametrize("raw, bare", [
    ("claude-opus-5-5", "claude-opus-5-5"),
    ("claude-opus-4-6[1m]", "claude-opus-4-6"),
    ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", "claude-sonnet-4-5-20250929"),
    ("anthropic/claude-haiku-4-5", "claude-haiku-4-5"),
    ("  Claude-Sonnet-5-5 ", "claude-sonnet-5-5"),
    (None, ""),
])
def test_normalize_strips_routing_and_qualifiers(raw, bare):
    assert normalize_model(raw) == bare


def test_longest_prefix_wins():
    assert price_for("claude-opus-4-20250514").input_per_m == 15.0
    assert price_for("claude-opus-4-5-20251101").input_per_m == 5.0
    assert price_for("claude-opus-5-5").input_per_m == 4.0
    assert price_for("claude-opus-5").input_per_m == 5.0
    assert price_for("claude-sonnet-4-5@20250929").input_per_m == 3.0


def test_cache_read_floor_is_reported():
    p = price_for("claude-fable-5-1")
    assert (p.input_per_m, p.cache_read_per_m, p.source) == (10.0, 0.25, "list")


def test_a_prefix_is_not_a_partial_word_match():
    # "claude-opus-5" must not price a hypothetical "claude-opus-50".
    assert price_for("claude-opus-50") is None


def test_unknown_models_are_not_guessed():
    assert price_for("gpt-5.5-codex") is None
    assert price_for("") is None


def test_config_override_wins_and_extends(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"model_prices": {
        "gpt-5.5": {"input": 1.25, "cache_read": 0.125},
        "claude-opus-5-5": {"input": 3.0},
        "broken": {"cache_read": 1},
    }}))
    gpt = price_for("gpt-5.5-codex")
    assert (gpt.input_per_m, gpt.cache_read_per_m, gpt.source) == (1.25, 0.125, "config")
    opus = price_for("claude-opus-5-5")
    assert (opus.input_per_m, opus.cache_read_per_m, opus.source) == (3.0, 3.0, "config")
    assert price_for("broken") is None


# --- ledger and endpoint --------------------------------------------------


def _seed(db):
    from memor.store.sqlite_store import SqliteStore

    store = SqliteStore(db, dim=8)
    now = time.time()
    rows = [
        # 2M saved on Opus 5.5 ($4/M) yesterday.
        {"provider": "hook", "model": "claude-opus-5-5",
         "tokens_before": 2_100_000, "tokens_after": 100_000, "timestamp": now - 86400},
        # 1M saved on an unpriced model today.
        {"provider": "responses", "model": "gpt-5.5-codex",
         "tokens_before": 1_500_000, "tokens_after": 500_000, "timestamp": now - 60},
        # 1M saved before models were recorded, 40 days ago.
        {"provider": "hook", "tokens_before": 1_000_000, "tokens_after": 0,
         "timestamp": now - 40 * 86400},
        # Passthrough rows never count.
        {"provider": "anthropic", "model": "claude-opus-5-5", "passthrough": 1,
         "tokens_before": 9_000_000, "tokens_after": 9_000_000, "timestamp": now - 30},
    ]
    for row in rows:
        store.record_proxy_savings({"agent": "claude", **row})
    return store


def test_ledger_groups_savings_by_model(tmp_path):
    store = _seed(str(tmp_path / "m.db"))
    by_model = {r["model"]: r["tokens_saved"] for r in store.get_savings_by_model()}
    assert by_model == {"claude-opus-5-5": 2_000_000, "gpt-5.5-codex": 1_000_000,
                        None: 1_000_000}


def test_savings_value_prices_each_model_and_leaves_the_rest_to_the_page(tmp_path):
    from memor.dashboard.server import create_app

    db = str(tmp_path / "m.db")
    _seed(db)
    data = TestClient(create_app(db_path=db)).get("/api/savings-value?days=365").json()

    life = data["lifetime"]
    assert life["tokens_saved"] == 4_000_000
    assert life["tokens_unpriced"] == 2_000_000
    assert life["usd_priced"] == pytest.approx(8.0)
    assert life["usd_floor_priced"] == pytest.approx(0.4)

    models = {m["model"]: m for m in data["models"]}
    assert models["claude-opus-5-5"]["usd"] == pytest.approx(8.0)
    assert models["claude-opus-5-5"]["price"]["input_per_m"] == 4.0
    assert models["gpt-5.5-codex"]["price"] is None
    assert models[None]["usd"] is None

    assert sum(b["tokens_saved"] for b in data["daily"]) == 4_000_000
    assert sum(b["usd_priced"] for b in data["monthly"]) == pytest.approx(8.0)
    assert {"weekly", "note"} <= set(data)


def test_savings_value_window_limits_periods_not_lifetime(tmp_path):
    from memor.dashboard.server import create_app

    db = str(tmp_path / "m.db")
    _seed(db)
    data = TestClient(create_app(db_path=db)).get("/api/savings-value?days=7").json()
    assert sum(b["tokens_saved"] for b in data["daily"]) == 3_000_000
    assert data["lifetime"]["tokens_saved"] == 4_000_000


def test_savings_value_on_an_empty_ledger(tmp_path):
    from memor.dashboard.server import create_app

    data = TestClient(create_app(db_path=str(tmp_path / "m.db"))).get(
        "/api/savings-value").json()
    assert data["lifetime"]["tokens_saved"] == 0
    assert data["models"] == [] and data["daily"] == []


def test_hook_rows_record_the_model_from_the_transcript(tmp_path):
    from memor.posttool_compress import _transcript_model

    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(r) for r in [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "message": {"role": "assistant", "model": "claude-sonnet-5-5"}},
        {"type": "assistant", "message": {"role": "assistant", "model": "<synthetic>"}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result"}]}},
    ]) + "\n")
    assert _transcript_model(str(transcript)) == "claude-sonnet-5-5"
    assert _transcript_model(None) is None
    assert _transcript_model(str(tmp_path / "missing.jsonl")) is None
