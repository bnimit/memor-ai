"""Counterfactual eval harness — win/tie/loss vs no-memory baseline.

For each held-out session, uses the opening turn as query, retrieves context
from prior sessions, and asks an LLM judge whether the recalled context would
have helped (win), been irrelevant (tie), or misled the agent (loss).

The key metric is do-no-harm rate = 1 - loss_rate."""
from __future__ import annotations

import enum
import json
import re
import time
from dataclasses import dataclass

from memor.types import Artifact


class Outcome(enum.Enum):
    WIN = "win"
    TIE = "tie"
    LOSS = "loss"


@dataclass
class CounterfactualCase:
    query: str
    holdout_texts: list[str]
    scope_project: str
    session_id: str


@dataclass
class CounterfactualVerdict:
    outcome: Outcome
    reasoning: str
    confidence: float


COUNTERFACTUAL_PROMPT = """You are evaluating whether recalled memory context helped or hurt a coding agent.

The agent received this task:
---
{query}
---

Here is what actually happened next in the session (ground truth — the agent didn't see this):
---
{holdout}
---

Here is the context that was recalled from prior sessions and injected into the agent's prompt:
---
{recalled_context}
---

Compare the recalled context against what actually happened. Score as:

- **win**: The recalled context directly anticipates what happened, would save the agent exploration/time, or would prevent repeating a past mistake. The agent would have been measurably better with this context.
- **tie**: The recalled context is irrelevant to what happened. Neither helpful nor harmful — the agent would have arrived at the same outcome either way.
- **loss**: The recalled context is stale, contradictory, or misleading. It would have sent the agent down the wrong path or caused confusion.

When uncertain, default to **tie** — only score win when the context clearly helps, and loss when it clearly hurts.

Return STRICT JSON: {{"outcome": "win"|"tie"|"loss", "reasoning": "<one sentence>", "confidence": <float 0-1>}}"""


def build_cases_from_store(store, *, project: str,
                           holdout_turns: int = 2,
                           min_session_turns: int = 4,
                           min_prior_sessions: int = 1) -> list[CounterfactualCase]:
    rows = store.db.execute(
        "SELECT * FROM artifacts WHERE project=? AND kind='session_chunk' AND active=1",
        (project,)).fetchall()
    artifacts = [store._row_to_artifact(r) for r in rows]

    by_session: dict[str, list[Artifact]] = {}
    for a in artifacts:
        by_session.setdefault(a.meta.get("session_id", "?"), []).append(a)
    for v in by_session.values():
        v.sort(key=lambda a: a.meta.get("ord", 0))
    sessions = sorted(by_session.items(), key=lambda kv: kv[1][0].created_at)

    cases: list[CounterfactualCase] = []
    for idx, (sid, chunks) in enumerate(sessions):
        if idx < min_prior_sessions:
            continue
        if len(chunks) < min_session_turns:
            continue
        query = chunks[0].text
        holdout = [c.text for c in chunks[-holdout_turns:]]
        cases.append(CounterfactualCase(
            query=query, holdout_texts=holdout,
            scope_project=project, session_id=sid))
    return cases


def _extract_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        return json.loads(m.group())
    return json.loads(raw)


def parse_verdict_json(raw: str) -> CounterfactualVerdict:
    data = _extract_json(raw)
    outcome_str = data.get("outcome", "tie").lower().strip()
    outcome = Outcome(outcome_str) if outcome_str in ("win", "tie", "loss") else Outcome.TIE
    return CounterfactualVerdict(
        outcome=outcome,
        reasoning=data.get("reasoning", ""),
        confidence=float(data.get("confidence", 0.5)),
    )


def run_case(case: CounterfactualCase, *, store, embedder, llm, db_path,
             k: int = 8) -> CounterfactualVerdict:
    """Judge a case using the PRODUCTION recall() path, not a bare Retriever.

    This mirrors what the hook actually injects: same-session exclusion,
    the 0.3/0.15 score threshold, the per-tier token budget, and 600-char
    truncation. Without this, the eval over-recalls (self-recall echoes and
    sub-threshold hits production would never inject), inflating ties and
    producing same-session "losses" that cannot happen in production.
    """
    from memor.query_complexity import route_query, Tier
    from memor.recall import recall

    tier = route_query(case.query)
    if tier == Tier.SKIP:
        return CounterfactualVerdict(
            outcome=Outcome.TIE,
            reasoning="Production routes this query to SKIP — no recall would occur",
            confidence=1.0)

    result = recall(case.query, case.scope_project, db_path, embedder=embedder,
                    k=tier.k, threshold=0.15, max_tokens=tier.max_tokens,
                    session_id=case.session_id)

    if not result.hit_ids:
        return CounterfactualVerdict(
            outcome=Outcome.TIE,
            reasoning="No context recalled via production path — nothing to evaluate",
            confidence=1.0)

    # Judge exactly what production injects (post-exclusion/threshold/budget/truncation).
    recalled = result.formatted_context
    holdout = "\n".join(case.holdout_texts)

    prompt = COUNTERFACTUAL_PROMPT.format(
        query=case.query, holdout=holdout, recalled_context=recalled)
    raw = llm.complete(prompt)
    return parse_verdict_json(raw)


def run_suite(cases: list[CounterfactualCase], *, store, embedder, llm, db_path,
              k: int = 8) -> dict:
    verdicts = []
    n = len(cases)
    for i, c in enumerate(cases, 1):
        print(f"  [{i}/{n}] judging case...", end="", flush=True)
        v = run_case(c, store=store, embedder=embedder, llm=llm, db_path=db_path, k=k)
        print(f" {v.outcome.value}", flush=True)
        verdicts.append((c, v))
    verdict_list = [v for _, v in verdicts]
    summary = summarize_verdicts(verdict_list)
    summary["cases"] = [
        {"session_id": c.session_id, "query_preview": c.query[:100],
         "outcome": v.outcome.value, "reasoning": v.reasoning,
         "confidence": v.confidence}
        for c, v in verdicts
    ]
    return summary


def run_suite_for_project(store, embedder, llm, *, project, arms, db_path=None, k=8):
    """Run the counterfactual suite once per arm on a single sample project,
    toggling the distillation/injection env flags so the Phase-A gate can
    compare raw-chunk vs distilled recall on identical cases."""
    import os
    _ARMS = {
        "raw": {"MEMOR_LLM_DISTILL": "0"},
        "distilled_fact": {"MEMOR_LLM_DISTILL": "1", "MEMOR_INJECT_MODE": "fact"},
        "distilled_value": {"MEMOR_LLM_DISTILL": "1", "MEMOR_INJECT_MODE": "value"},
    }
    results = {}
    for arm in arms:
        saved = {k_: os.environ.get(k_) for k_ in _ARMS[arm]}
        os.environ.update(_ARMS[arm])
        try:
            cases = build_cases_from_store(store, project=project)
            results[arm] = run_suite(cases, store=store, embedder=embedder,
                                     llm=llm, db_path=db_path, k=k)
        finally:
            for k_, v in saved.items():
                if v is None:
                    os.environ.pop(k_, None)
                else:
                    os.environ[k_] = v
    return results


def summarize_verdicts(verdicts: list[CounterfactualVerdict]) -> dict:
    n = len(verdicts)
    if n == 0:
        return {"n_cases": 0, "win_count": 0, "tie_count": 0, "loss_count": 0,
                "win_pct": 0.0, "tie_pct": 0.0, "loss_pct": 0.0,
                "do_no_harm_pct": 100.0}

    wins = sum(1 for v in verdicts if v.outcome == Outcome.WIN)
    ties = sum(1 for v in verdicts if v.outcome == Outcome.TIE)
    losses = sum(1 for v in verdicts if v.outcome == Outcome.LOSS)

    return {
        "n_cases": n,
        "win_count": wins,
        "tie_count": ties,
        "loss_count": losses,
        "win_pct": round(wins / n * 100, 1),
        "tie_pct": round(ties / n * 100, 1),
        "loss_pct": round(losses / n * 100, 1),
        "do_no_harm_pct": round((wins + ties) / n * 100, 1),
    }
