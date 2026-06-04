from memor.eval.embed_benchmark import (
    EmbedBenchmarkResult, run_embed_benchmark, CANDIDATE_MODELS,
)
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.eval.dataset import EvalCase
from memor.types import Artifact


def _chunk(i, text, t):
    return Artifact(
        id=f"s1:{i}", kind="session_chunk", project="p", source="cc",
        text=text, token_count=len(text.split()), created_at=t,
        meta={"session_id": "s1", "role": "assistant", "ord": i},
    )


def test_candidate_models_is_list():
    assert isinstance(CANDIDATE_MODELS, list)
    assert len(CANDIDATE_MODELS) >= 2
    for m in CANDIDATE_MODELS:
        assert "name" in m
        assert "model_name" in m


def test_run_embed_benchmark_with_fake(tmp_path):
    arts = [
        _chunk(0, "auth refresh token loop fix applied", 100),
        _chunk(1, "unrelated css styling tweak for header", 90),
        _chunk(2, "auth token rotation decision using argon2", 80),
    ]
    cases = [
        EvalCase(query="auth refresh", scope_project="p",
                 relevant_ids={"s1:0", "s1:2"}, baseline_full_tokens=1000)
    ]

    def fake_factory(model_spec):
        return FakeEmbedder(dim=16)

    results = run_embed_benchmark(
        arts, cases,
        model_specs=[{"name": "fake-a", "model_name": "fake"}, {"name": "fake-b", "model_name": "fake"}],
        embedder_factory=fake_factory,
        db_dir=str(tmp_path),
    )
    assert len(results) == 2
    for r in results:
        assert isinstance(r, EmbedBenchmarkResult)
        assert r.model_name
        assert r.recall_at_k >= 0
        assert r.ndcg_at_k >= 0
        assert r.embed_latency_ms >= 0
