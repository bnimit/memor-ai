from __future__ import annotations
from memorable.types import Scope
from memorable.retrieve.retriever import Retriever
from memorable.eval.dataset import EvalCase, CaseResult
from memorable.eval.metrics import recall_at_k, ndcg_at_k

BASELINES = ["no-memory", "naive-RAG", "memory"]

def _result(ranked_ids, hits_tokens, latency, case, k):
    return CaseResult(
        recall=recall_at_k(ranked_ids, case.relevant_ids, k),
        ndcg=ndcg_at_k(ranked_ids, case.relevant_ids, k),
        tokens_sent=hits_tokens, latency_ms=latency)

def run_case(case: EvalCase, *, store, embedder, k: int = 8) -> dict[str, CaseResult]:
    scope = Scope(project=case.scope_project)
    out: dict[str, CaseResult] = {}

    # no-memory: retrieve nothing
    out["no-memory"] = _result([], 0, 0.0, case, k)

    # naive-RAG: similarity only, no edges, no distill
    naive = Retriever(store, embedder, k=k, recency_weight=0.0, edge_expand=False)
    tr = naive.query(case.query, scope)
    out["naive-RAG"] = _result([h.artifact.id for h in tr.hits],
                               sum(h.artifact.token_count for h in tr.hits),
                               tr.latency_ms, case, k)

    # memory: full retriever (recency + edge expansion over distilled+raw artifacts)
    mem = Retriever(store, embedder, k=k, recency_weight=0.2, edge_expand=True)
    tr = mem.query(case.query, scope)
    out["memory"] = _result([h.artifact.id for h in tr.hits],
                            sum(h.artifact.token_count for h in tr.hits),
                            tr.latency_ms, case, k)
    return out

def run_suite(cases: list[EvalCase], *, store, embedder, k: int = 8) -> dict[str, dict]:
    """Aggregate mean metrics per strategy + Δ vs naive-RAG and full-history tokens."""
    agg: dict[str, list[CaseResult]] = {}
    for c in cases:
        for strat, res in run_case(c, store=store, embedder=embedder, k=k).items():
            agg.setdefault(strat, []).append(res)
    summary = {}
    n = len(cases) or 1
    full_tokens = sum(c.baseline_full_tokens for c in cases) / n
    for strat, results in agg.items():
        mean_tokens = sum(r.tokens_sent for r in results) / n
        summary[strat] = {
            "recall@k": sum(r.recall for r in results) / n,
            "ndcg@k": sum(r.ndcg for r in results) / n,
            "tokens_sent": mean_tokens,
            "token_savings_vs_full": 1.0 - (mean_tokens / full_tokens) if full_tokens else 0.0,
            "latency_ms_p50": sorted(r.latency_ms for r in results)[len(results)//2],
        }
    return summary
