from __future__ import annotations
import time
from dataclasses import dataclass
from memor.store.sqlite_store import SqliteStore
from memor.eval.runner import run_suite
from memor.eval.dataset import EvalCase
from memor.types import Artifact

CANDIDATE_MODELS = [
    {"name": "potion-base-8M", "model_name": "minishlab/potion-base-8M"},
    {"name": "potion-base-32M", "model_name": "minishlab/potion-base-32M"},
    {"name": "potion-code-16M", "model_name": "minishlab/potion-code-16M"},
]


@dataclass
class EmbedBenchmarkResult:
    model_name: str
    dim: int
    recall_at_k: float
    ndcg_at_k: float
    tokens_sent: float
    embed_latency_ms: float
    retrieval_latency_ms: float


def run_embed_benchmark(
    artifacts: list[Artifact],
    cases: list[EvalCase],
    *,
    model_specs: list[dict] | None = None,
    embedder_factory=None,
    db_dir: str = "/tmp",
    k: int = 8,
) -> list[EmbedBenchmarkResult]:
    specs = model_specs or CANDIDATE_MODELS
    results = []

    for spec in specs:
        name = spec["name"]
        if embedder_factory:
            embedder = embedder_factory(spec)
        else:
            from memor.embed.local import LocalEmbedder
            embedder = LocalEmbedder(model_name=spec["model_name"])

        db_path = f"{db_dir}/bench_{name.replace('/', '_')}.db"
        store = SqliteStore(db_path, dim=embedder.dim)

        t0 = time.perf_counter()
        vecs = embedder.embed([a.text for a in artifacts])
        embed_ms = (time.perf_counter() - t0) * 1000

        store.add_artifacts(artifacts, vecs)
        summary = run_suite(cases, store=store, embedder=embedder, k=k)
        mem = summary.get("memory", {})

        results.append(EmbedBenchmarkResult(
            model_name=name,
            dim=embedder.dim,
            recall_at_k=mem.get("recall@k", 0.0),
            ndcg_at_k=mem.get("ndcg@k", 0.0),
            tokens_sent=mem.get("tokens_sent", 0),
            embed_latency_ms=embed_ms,
            retrieval_latency_ms=mem.get("latency_ms_p50", 0.0),
        ))
    return results
