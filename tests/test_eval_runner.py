from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.eval.dataset import EvalCase
from memor.eval.runner import run_case, BASELINES

def test_run_case_reports_metrics_per_strategy(tmp_path):
    from memor.types import Artifact
    e = FakeEmbedder(dim=16); s = SqliteStore(str(tmp_path/"m.db"), dim=16)
    def mk(i, text, t): return Artifact(id=i, kind="session_chunk", project="p",
        source="t", text=text, token_count=len(text.split()), created_at=t, meta={})
    arts = [mk("a1","auth refresh token loop fix",100),
            mk("a2","unrelated css styling tweak",90),
            mk("a3","auth token rotation decision",80)]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    case = EvalCase(query="auth refresh", scope_project="p",
                    relevant_ids={"a1","a3"}, baseline_full_tokens=1000)
    results = run_case(case, store=s, embedder=e, k=2)
    assert set(results.keys()) >= {"memory", "naive-RAG", "no-memory"}
    assert results["memory"].recall >= results["no-memory"].recall
    assert results["memory"].tokens_sent < case.baseline_full_tokens   # compaction wins on cost
