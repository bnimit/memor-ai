import os

import memor.eval.counterfactual as cf


def test_run_suite_for_project_sets_env_per_arm(monkeypatch):
    seen = []

    # Snapshot env before test
    distill_before = os.environ.get("MEMOR_LLM_DISTILL")
    inject_before = os.environ.get("MEMOR_INJECT_MODE")

    def fake_run_suite(cases, **kw):
        seen.append((os.environ.get("MEMOR_LLM_DISTILL"), os.environ.get("MEMOR_INJECT_MODE")))
        return {"win": 0, "tie": len(cases), "loss": 0, "do_no_harm_pct": 100.0}

    monkeypatch.setattr(cf, "build_cases_from_store", lambda *a, **k: [1, 2, 3])
    monkeypatch.setattr(cf, "run_suite", fake_run_suite)
    out = cf.run_suite_for_project(object(), object(), object(),
                                   project="p", arms=["raw", "distilled_fact"])

    # Verify env restored after harness returns
    assert os.environ.get("MEMOR_LLM_DISTILL") == distill_before
    assert os.environ.get("MEMOR_INJECT_MODE") == inject_before

    assert set(out) == {"raw", "distilled_fact"}
    assert ("0", None) in seen  # raw arm: distill off
    assert any(a == "1" and m == "fact" for a, m in seen)  # distilled_fact arm
