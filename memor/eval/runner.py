from __future__ import annotations
from memor.types import Scope
from memor.retrieve.retriever import Retriever
from memor.eval.dataset import EvalCase, CaseResult
from memor.eval.metrics import recall_at_k, ndcg_at_k

BASELINES = ["no-memory", "last-N", "naive-RAG", "memory"]

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

    # last-N: most recent k chunks, no vector search
    recent = store.recent(scope, k) if hasattr(store, 'recent') else []
    out["last-N"] = _result([a.id for a in recent],
                            sum(a.token_count for a in recent),
                            0.0, case, k)

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
        latencies = sorted(r.latency_ms for r in results)
        summary[strat] = {
            "recall@k": sum(r.recall for r in results) / n,
            "ndcg@k": sum(r.ndcg for r in results) / n,
            "tokens_sent": mean_tokens,
            "token_savings_vs_full": 1.0 - (mean_tokens / full_tokens) if full_tokens else 0.0,
            "latency_ms_p50": latencies[len(latencies)//2],
            "latency_ms_p95": latencies[int(len(latencies)*0.95)],
        }
    return summary

def run_ablation(*, query, project, relevant_ids, store, embedder, k=8):
    out = {}
    for name, expand in (("similarity-only", False), ("similarity+edges", True)):
        r = Retriever(store, embedder, k=k, recency_weight=0.0, edge_expand=expand)
        tr = r.query(query, Scope(project=project))
        ids = [h.artifact.id for h in tr.hits]
        out[name] = {"recall@k": recall_at_k(ids, relevant_ids, k),
                     "ndcg@k": ndcg_at_k(ids, relevant_ids, k)}
    return out

def run_contradiction_eval(*, query, project, stale_id, current_id, store, embedder, k=8):
    r = Retriever(store, embedder, k=k, edge_expand=True)
    ids = [h.artifact.id for h in r.query(query, Scope(project=project)).hits]
    return (current_id in ids) and (stale_id not in ids)
