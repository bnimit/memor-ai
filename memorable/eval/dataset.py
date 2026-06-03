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
