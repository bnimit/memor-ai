"""The randomized recall holdout: assignment, both write paths, and read-back.

The observational recall comparison can only bound recall's effect, because
recall fires on the prompts it matches. These tests pin the properties the
holdout's causal claim rests on: only matched prompts are assigned, a withheld
recall reaches neither the model nor any ledger that describes served memories,
and the arms are read back onto the right episodes.
"""
from __future__ import annotations

import json

import pytest

from memor import recall_experiment as rx
from memor.embed.fake import FakeEmbedder
from memor.episodes import (
    Episode, attach_experiment_arms, format_holdout, format_ledger, format_report,
    measure_holdout, parse_episodes, summarize, whole_product_ledger,
)
from memor.store.sqlite_store import SqliteStore
from memor.types import Artifact

QUERY = "how does password hashing work in the auth module?"


@pytest.fixture(autouse=True)
def _no_holdout_by_default(monkeypatch):
    monkeypatch.delenv(rx.ENV_VAR, raising=False)
    monkeypatch.delenv("MEMOR_HOLDOUT_FRACTION", raising=False)
    import memor.hook_server as hs
    hs._session_injected.clear()


def _store_with_memory(tmp_path, project="myproj"):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    art = Artifact(id="a1", kind="memory", project=project, source="distill",
                   text="we decided to use argon2 for password hashing in the auth module",
                   token_count=12, created_at=100.0,
                   meta={"mem_type": "decision", "session_id": "s1"})
    s.add_artifacts([art], e.embed([art.text]))
    return db_path, e, s


def _count(store, table):
    return store.db.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


# --- assignment ---------------------------------------------------------------

def test_off_by_default():
    assert rx.holdout_fraction() == 0.0
    assert not rx.is_enabled()
    assert rx.assign_arm("anything") == rx.ARM_TREATMENT


@pytest.mark.parametrize("raw,expected", [
    ("0.1", 0.1), ("0", 0.0), ("-1", 0.0), ("2", 1.0), ("junk", 0.0), ("", 0.0)])
def test_fraction_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv(rx.ENV_VAR, raw)
    assert rx.holdout_fraction() == expected


def test_assignment_is_deterministic_and_near_the_fraction():
    keys = [rx.experiment_key("s", "", f"prompt {i}") for i in range(4000)]
    first = [rx.assign_arm(k, fraction=0.25) for k in keys]
    assert first == [rx.assign_arm(k, fraction=0.25) for k in keys]
    share = first.count(rx.ARM_CONTROL) / len(first)
    assert 0.22 < share < 0.28


def test_independent_of_the_compression_holdout(monkeypatch):
    """Turning on one experiment must not switch on, or correlate with, the other."""
    monkeypatch.setenv("MEMOR_HOLDOUT_FRACTION", "0.5")
    assert not rx.is_enabled()
    from memor.proxy import experiment as cx

    keys = [f"k{i}" for i in range(4000)]
    both = sum(1 for k in keys
               if rx.assign_arm(k, fraction=0.5) == rx.ARM_CONTROL
               and cx.assign_arm(k, fraction=0.5) == cx.ARM_CONTROL)
    assert 0.21 < both / len(keys) < 0.29  # ~0.25 if independent, 0.5 if shared


def test_same_prompt_in_two_conversations_is_two_draws():
    assert rx.experiment_key("s1", "", "q") != rx.experiment_key("s2", "", "q")
    assert rx.experiment_key("", "convo", "q") == rx.experiment_key("", "convo", "q ")


# --- hook path ----------------------------------------------------------------

@pytest.fixture
def recall_hits(monkeypatch):
    """A recall that found one memory.

    The fake embedder's similarities are hash noise, so whether a real query
    clears the hook's gate is arbitrary. What happens after recall returns is
    what these tests are about.
    """
    from memor.recall import RecallResult

    def fake(*args, **kwargs):
        return RecallResult(
            hits_count=1, top_score=0.8, tokens_injected=42, latency_ms=1.0,
            status="ok", status_message="Memor: 1 memory",
            formatted_context="## Recalled Memories (project: myproj)\n- argon2\n---\nMemor: 1 memory",
            hit_ids=["a1"])

    monkeypatch.setattr("memor.recall.recall", fake)


def _hook(db_path, e, session="sess-1", prompt=QUERY):
    from memor.hook_server import handle_request

    req = {"prompt": prompt, "cwd": "/tmp/myproj", "session_id": session}
    return handle_request(req, db_path=db_path, embedder=e)


def test_hook_withheld_injects_nothing_and_touches_no_served_ledger(tmp_path, monkeypatch, recall_hits):
    import memor.hook_server as hs

    db_path, e, s = _store_with_memory(tmp_path)
    monkeypatch.setenv(rx.ENV_VAR, "1")
    ctx = _hook(db_path, e)["hookSpecificOutput"]["additionalContext"]

    assert ctx == ""
    rows = s.load_recall_experiment()
    assert [r["arm"] for r in rows] == [rx.ARM_CONTROL]
    assert rows[0]["hits_count"] == 1 and rows[0]["tokens_offered"] == 42
    assert rows[0]["session_id"] == "sess-1"
    # Never shown, so never counted as injected, recalled, or awaiting a verdict.
    assert _count(s, "recall_log") == 0
    assert _count(s, "recall_outcomes") == 0
    assert "sess-1" not in hs._session_injected


def test_hook_treatment_serves_and_records_its_arm(tmp_path, monkeypatch, recall_hits):
    db_path, e, s = _store_with_memory(tmp_path)
    monkeypatch.setenv(rx.ENV_VAR, "1e-12")
    ctx = _hook(db_path, e)["hookSpecificOutput"]["additionalContext"]

    assert "## Recalled Memories" in ctx
    assert [r["arm"] for r in s.load_recall_experiment()] == [rx.ARM_TREATMENT]
    assert _count(s, "recall_log") == 1


def test_hook_without_experiment_writes_no_rows(tmp_path, recall_hits):
    db_path, e, s = _store_with_memory(tmp_path)
    ctx = _hook(db_path, e)["hookSpecificOutput"]["additionalContext"]
    assert "## Recalled Memories" in ctx
    assert s.load_recall_experiment() == []


def test_hook_recall_that_found_nothing_is_not_assigned(tmp_path, monkeypatch):
    db_path = str(tmp_path / "m.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    monkeypatch.setenv(rx.ENV_VAR, "1")
    _hook(db_path, e)
    assert s.load_recall_experiment() == []


def test_hook_serves_rather_than_withhold_unrecorded(tmp_path, monkeypatch, recall_hits):
    """A withheld recall nobody wrote down is a loss that teaches nothing."""
    db_path, e, s = _store_with_memory(tmp_path)
    monkeypatch.setenv(rx.ENV_VAR, "1")
    monkeypatch.setattr(SqliteStore, "log_recall_experiment", lambda self, **kw: False)
    ctx = _hook(db_path, e)["hookSpecificOutput"]["additionalContext"]
    assert "## Recalled Memories" in ctx


# --- proxy path ---------------------------------------------------------------

def _body():
    return {"model": "claude-sonnet-4-0", "max_tokens": 64,
            "messages": [{"role": "user", "content": QUERY}]}


def test_proxy_withheld_leaves_body_untouched(tmp_path, monkeypatch):
    from memor.proxy.memory import inject_memory

    db_path, e, s = _store_with_memory(tmp_path)
    monkeypatch.setenv(rx.ENV_VAR, "1")
    body = _body()
    out = inject_memory("anthropic", body, project="myproj", db_path=db_path,
                        embedder=e, store=s, agent="claude")
    assert out == body
    rows = s.load_recall_experiment()
    assert [r["arm"] for r in rows] == [rx.ARM_CONTROL]
    assert rows[0]["conversation_key"]
    assert _count(s, "recall_log") == 0


def test_proxy_treatment_injects_and_records(tmp_path, monkeypatch):
    from memor.proxy.memory import inject_memory

    db_path, e, s = _store_with_memory(tmp_path)
    monkeypatch.setenv(rx.ENV_VAR, "1e-12")
    out = inject_memory("anthropic", _body(), project="myproj", db_path=db_path,
                        embedder=e, store=s, agent="claude")
    assert "## Recalled Memories" in out["messages"][-1]["content"]
    assert [r["arm"] for r in s.load_recall_experiment()] == [rx.ARM_TREATMENT]


def test_proxy_without_store_never_withholds(tmp_path, monkeypatch):
    from memor.proxy.memory import inject_memory

    db_path, e, _ = _store_with_memory(tmp_path)
    monkeypatch.setenv(rx.ENV_VAR, "1")
    out = inject_memory("anthropic", _body(), project="myproj", db_path=db_path, embedder=e)
    assert "## Recalled Memories" in out["messages"][-1]["content"]


# --- read-back ----------------------------------------------------------------

def _ep(start, prompt="", session="s", convo="", steps=3, tools=4, inp=1000):
    return Episode(session_id=session, conversation_key=convo, started_at=start,
                   prompt_chars=len(prompt), prompt_preview=prompt,
                   assistant_steps=steps, tool_calls=tools, input_tokens=inp)


def test_rows_attach_by_prompt_text_before_time():
    """A hook row stamped just after the next prompt still finds its own episode."""
    a, b = _ep(100, "explain the retry logic"), _ep(101, "now fix the flaky test")
    rows = [{"timestamp": 101.5, "arm": "withheld", "session_id": "s",
             "query_preview": "explain the retry logic"}]
    attach_experiment_arms([a, b], rows)
    assert a.recall_arm == "withheld" and b.recall_arm is None


def test_short_preview_must_match_exactly():
    a = _ep(100, "fix the login bug")
    rows = [{"timestamp": 100.1, "arm": "recall", "session_id": "s", "query_preview": "fix"}]
    attach_experiment_arms([a], rows)
    # No text match, so it falls back to time, which still finds the episode.
    assert a.recall_arm == "recall"
    b, c = _ep(100, "fix"), _ep(200, "fix the login bug")
    rows = [{"timestamp": 200.1, "arm": "recall", "session_id": "s", "query_preview": "fix"}]
    attach_experiment_arms([b, c], rows)
    assert b.recall_arm == "recall" and c.recall_arm is None


def test_proxy_rows_attach_by_conversation_key():
    a = _ep(100, "q one", session="transcript-stem", convo="ck")
    rows = [{"timestamp": 130, "arm": "withheld", "session_id": "",
             "conversation_key": "ck", "query_preview": "q one"}]
    attach_experiment_arms([a], rows)
    assert a.recall_arm == "withheld"


def test_episode_with_both_arms_is_excluded():
    a = _ep(100, "same words")
    rows = [{"timestamp": 100, "arm": "recall", "session_id": "s", "query_preview": "same words"},
            {"timestamp": 101, "arm": "withheld", "session_id": "s", "query_preview": "same words"}]
    assert attach_experiment_arms([a], rows) == 1
    assert a.recall_arm is None


def test_transcript_round_trip(tmp_path):
    """Arms land on episodes parsed from a real-shaped Claude Code transcript."""
    lines = []
    for i, prompt in enumerate(["first prompt here", "second prompt here"]):
        lines.append({"type": "user", "timestamp": f"2026-10-11T10:0{i}:00Z",
                      "message": {"role": "user", "content": prompt}})
        lines.append({"type": "assistant", "message": {
            "usage": {"input_tokens": 100, "output_tokens": 10},
            "content": [{"type": "tool_use", "name": "Bash"}]}})
    path = tmp_path / "sess-xyz.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines))
    episodes = parse_episodes(path)
    second_start = episodes[1].started_at
    attach_experiment_arms(episodes, [{
        "timestamp": second_start + 0.4, "arm": "withheld",
        "session_id": "sess-xyz", "query_preview": "second prompt here"}])
    assert [e.recall_arm for e in episodes] == [None, "withheld"]


# --- the measured comparison --------------------------------------------------

def _arm(arm, n, inp, jitter=0):
    out = []
    for i in range(n):
        e = _ep(i, f"p{i}", inp=inp + (i % 5 - 2) * jitter)
        e.recall_arm = arm
        out.append(e)
    return out


def test_not_running_without_rows():
    assert measure_holdout([_ep(1)])["verdict"] == "not_running"


def test_insufficient_below_the_floor():
    m = measure_holdout(_arm("recall", 10, 800) + _arm("withheld", 10, 1000))
    assert m["verdict"] == "insufficient_data"


def test_saves_when_recall_arm_is_clearly_cheaper():
    m = measure_holdout(_arm("recall", 40, 700, jitter=20) + _arm("withheld", 40, 1000, jitter=20))
    assert m["verdict"] == "saves"
    assert m["cost_delta_pct"] == pytest.approx(30.0, abs=1)
    assert m["avoided_units_per_episode"] == pytest.approx(300, abs=5)


def test_costs_when_recall_arm_is_dearer():
    m = measure_holdout(_arm("recall", 40, 1300, jitter=20) + _arm("withheld", 40, 1000, jitter=20))
    assert m["verdict"] == "costs"


def test_no_effect_only_when_the_experiment_could_have_seen_20pct():
    m = measure_holdout(_arm("recall", 40, 1000, jitter=10) + _arm("withheld", 40, 1000, jitter=10))
    assert m["verdict"] == "no_effect" and m["mde_pct"] < 20


def test_unresolved_when_too_noisy_to_rule_out_20pct():
    m = measure_holdout(_arm("recall", 30, 1000, jitter=900) + _arm("withheld", 30, 1050, jitter=900))
    assert m["verdict"] == "unresolved"
    assert m["n_needed_per_arm"] > 30
    assert "would resolve" in "\n".join(format_holdout(m))


def test_report_carries_the_measured_section():
    episodes = _arm("recall", 40, 700, jitter=20) + _arm("withheld", 40, 1000, jitter=20)
    text = "\n".join(format_report(summarize(episodes)))
    assert "MEASURED (randomized recall holdout)" in text
    assert "recall makes episodes cheaper" in text


# --- whole-product ledger -----------------------------------------------------

def _ledger_store(tmp_path):
    s = SqliteStore(str(tmp_path / "l.db"), dim=16)
    s.record_proxy_savings({"timestamp": 1.0, "agent": "claude", "provider": "hook",
                            "tokens_before": 10_000, "tokens_after": 4_000,
                            "content_types": {"log": 1}, "passthrough": 0})
    s.log_recall(project="p", query_preview="q", hits_count=2, top_score=0.5,
                 tokens_injected=1_500, latency_ms=1.0, status="ok")
    return s


def test_ledger_counts_recall_as_cost_until_measured(tmp_path):
    ledger = whole_product_ledger(_ledger_store(tmp_path), {"verdict": "unresolved"}, 10)
    assert ledger["compression_saved"] == 6_000
    assert ledger["recall_injected"] == 1_500
    assert ledger["net_counted"] == 4_500
    assert "net_with_measured_recall" not in ledger
    assert "not counted until the holdout" in "\n".join(format_ledger(ledger))


def test_ledger_adds_measured_recall_without_double_counting(tmp_path):
    measured = {"verdict": "saves", "avoided_units_per_episode": 300.0}
    ledger = whole_product_ledger(_ledger_store(tmp_path), measured, 10)
    assert ledger["recall_extrapolated"] == 3_000
    # Net of injection already, so the 1,500 injected is not subtracted again.
    assert ledger["net_with_measured_recall"] == 9_000
    assert "extrapolated" in "\n".join(format_ledger(ledger))
