from __future__ import annotations
import json, re, time
from dataclasses import dataclass
from memor.types import Artifact, Scope
from memor.retrieve.retriever import Retriever

JUDGE_PROMPT = """You are evaluating whether recalled memory context would help a coding agent.

The agent is about to work on this task:
---
{query}
---

Here is what actually happened next in the session (the agent doesn't see this — you use it to judge):
---
{holdout}
---

Here is the context that was recalled from prior sessions:
---
{recalled_context}
---

Score how relevant and useful the recalled context is for the task, given what actually happened.
- 1.0 = recalled context directly addresses or anticipates what happened
- 0.7 = recalled context is clearly relevant and would save the agent time
- 0.4 = recalled context is somewhat related but not directly useful
- 0.1 = recalled context is mostly irrelevant
- 0.0 = no recalled context, or completely irrelevant

Return STRICT JSON: {{"relevance_score": <float 0-1>, "reasoning": "<one sentence>"}}"""


@dataclass
class JudgeCase:
    query: str
    holdout_texts: list[str]
    scope_project: str
    session_id: str
    baseline_full_tokens: int


@dataclass
class JudgeVerdict:
    relevance_score: float
    reasoning: str
    tokens_recalled: int
    latency_ms: float


def build_judge_cases(
    artifacts: list[Artifact], *, project: str,
    holdout_turns: int = 2, min_session_turns: int = 4,
    min_prior_sessions: int = 1,
) -> list[JudgeCase]:
    by_session: dict[str, list[Artifact]] = {}
    for a in artifacts:
        by_session.setdefault(a.meta.get("session_id", "?"), []).append(a)
    for v in by_session.values():
        v.sort(key=lambda a: a.meta.get("ord", 0))
    sessions = sorted(by_session.items(), key=lambda kv: kv[1][0].created_at)

    cases: list[JudgeCase] = []
    for idx, (sid, chunks) in enumerate(sessions):
        if idx < min_prior_sessions:
            continue
        if len(chunks) < min_session_turns:
            continue
        query = chunks[0].text
        holdout = [c.text for c in chunks[-holdout_turns:]]
        prior_chunks = [a for j, (_, cs) in enumerate(sessions) if j < idx for a in cs]
        full_tokens = sum(a.token_count for a in prior_chunks)
        cases.append(JudgeCase(
            query=query, holdout_texts=holdout, scope_project=project,
            session_id=sid, baseline_full_tokens=full_tokens,
        ))
    return cases


def _extract_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        return json.loads(m.group())
    return json.loads(raw)


def run_judge_case(
    case: JudgeCase, *, store, embedder, llm, k: int = 8
) -> JudgeVerdict:
    scope = Scope(project=case.scope_project)
    r = Retriever(store, embedder, k=k)
    t0 = time.perf_counter()
    trace = r.query(case.query, scope)
    latency = (time.perf_counter() - t0) * 1000

    if not trace.hits:
        return JudgeVerdict(relevance_score=0.0, reasoning="No context recalled",
                            tokens_recalled=0, latency_ms=latency)

    recalled = "\n\n".join(
        f"[{h.artifact.kind}] {h.artifact.text}" for h in trace.hits
    )
    tokens_recalled = sum(h.artifact.token_count for h in trace.hits)
    holdout = "\n".join(case.holdout_texts)

    prompt = JUDGE_PROMPT.format(
        query=case.query, holdout=holdout, recalled_context=recalled,
    )
    raw = llm.complete(prompt)
    data = _extract_json(raw)
    return JudgeVerdict(
        relevance_score=float(data.get("relevance_score", 0.0)),
        reasoning=data.get("reasoning", ""),
        tokens_recalled=tokens_recalled,
        latency_ms=latency,
    )


def run_judge_suite(
    cases: list[JudgeCase], *, store, embedder, llm, k: int = 8
) -> dict:
    verdicts = []
    for c in cases:
        v = run_judge_case(c, store=store, embedder=embedder, llm=llm, k=k)
        verdicts.append(v)
    n = len(verdicts) or 1
    return {
        "mean_relevance": sum(v.relevance_score for v in verdicts) / n,
        "mean_tokens_recalled": sum(v.tokens_recalled for v in verdicts) / n,
        "mean_latency_ms": sum(v.latency_ms for v in verdicts) / n,
        "n_cases": len(verdicts),
        "verdicts": [
            {"score": v.relevance_score, "reasoning": v.reasoning,
             "tokens": v.tokens_recalled, "latency_ms": v.latency_ms}
            for v in verdicts
        ],
    }
