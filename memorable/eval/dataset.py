from __future__ import annotations
from dataclasses import dataclass

@dataclass
class EvalCase:
    query: str
    scope_project: str
    relevant_ids: set[str]
    baseline_full_tokens: int   # tokens if you dumped full history instead

@dataclass
class CaseResult:
    recall: float
    ndcg: float
    tokens_sent: int
    latency_ms: float

import re
from memorable.types import Artifact

_WORD = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{3,}")

def build_counterfactual_cases(artifacts: list["Artifact"], *, project: str,
                               min_prior_sessions: int = 1) -> list[EvalCase]:
    """For each session N (by start time), use its opening turn as the query and
    label prior-session chunks that share salient tokens as the context it needed."""
    by_session: dict[str, list[Artifact]] = {}
    for a in artifacts:
        by_session.setdefault(a.meta["session_id"], []).append(a)
    for v in by_session.values():
        v.sort(key=lambda a: a.meta.get("ord", 0))
    sessions = sorted(by_session.items(), key=lambda kv: kv[1][0].created_at)

    cases: list[EvalCase] = []
    for idx, (sid, chunks) in enumerate(sessions):
        if idx < min_prior_sessions:
            continue
        opening = chunks[0].text
        need_tokens = {w.lower() for w in _WORD.findall(opening)}
        prior = [a for j, (_, cs) in enumerate(sessions) if j < idx for a in cs]
        relevant = {a.id for a in prior
                    if need_tokens & {w.lower() for w in _WORD.findall(a.text)}}
        if not relevant:
            continue
        full_tokens = sum(a.token_count for a in prior)
        cases.append(EvalCase(query=opening, scope_project=project,
                              relevant_ids=relevant, baseline_full_tokens=full_tokens))
    return cases
