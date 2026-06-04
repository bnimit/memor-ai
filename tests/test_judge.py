import json
from memor.eval.judge import build_judge_cases, JudgeCase, run_judge_case, JudgeVerdict
from memor.store.sqlite_store import SqliteStore
from memor.embed.fake import FakeEmbedder
from memor.types import Artifact


def _chunk(session_id, i, text, t):
    return Artifact(
        id=f"{session_id}:{i}", kind="session_chunk", project="p", source="cc",
        text=text, token_count=len(text.split()), created_at=t,
        meta={"session_id": session_id, "role": "user" if i == 0 else "assistant", "ord": i},
    )


def test_build_judge_cases_creates_holdout():
    chunks = [
        _chunk("s1", 0, "set up the database schema for users", 100),
        _chunk("s1", 1, "CREATE TABLE users (id INT, name TEXT, email TEXT)", 101),
        _chunk("s1", 2, "add an index on the email column", 102),
        _chunk("s1", 3, "CREATE INDEX idx_email ON users(email)", 103),
        _chunk("s2", 0, "now add auth to the users table", 200),
        _chunk("s2", 1, "ALTER TABLE users ADD COLUMN password_hash TEXT", 201),
        _chunk("s2", 2, "use argon2 for the hashing", 202),
        _chunk("s2", 3, "updated the auth module to use argon2 for all password hashing", 203),
    ]
    cases = build_judge_cases(chunks, project="p", holdout_turns=2)
    assert len(cases) >= 1
    case = cases[0]
    assert isinstance(case, JudgeCase)
    assert case.query
    assert len(case.holdout_texts) == 2
    assert case.scope_project == "p"


def test_build_judge_cases_skips_short_sessions():
    chunks = [
        _chunk("s1", 0, "hello", 100),
        _chunk("s1", 1, "hi there", 101),
        _chunk("s2", 0, "how do I fix auth", 200),
        _chunk("s2", 1, "check the refresh token logic", 201),
    ]
    cases = build_judge_cases(chunks, project="p", holdout_turns=2)
    assert len(cases) == 0


def test_run_judge_case_with_fake_llm(tmp_path):
    e = FakeEmbedder(dim=16)
    s = SqliteStore(str(tmp_path / "m.db"), dim=16)

    prior_chunks = [
        _chunk("s1", 0, "set up the database schema for users table", 100),
        _chunk("s1", 1, "CREATE TABLE users with id name email columns", 101),
    ]
    s.add_artifacts(prior_chunks, e.embed([c.text for c in prior_chunks]))

    case = JudgeCase(
        query="now add auth to the users table",
        holdout_texts=["use argon2 for the hashing", "updated auth module to use argon2"],
        scope_project="p",
        session_id="s2",
        baseline_full_tokens=200,
    )

    class FakeLLM:
        def complete(self, prompt, *, max_tokens=1024):
            return json.dumps({
                "relevance_score": 0.7,
                "reasoning": "The recalled context mentions the users table schema which is relevant."
            })

    verdict = run_judge_case(case, store=s, embedder=e, llm=FakeLLM(), k=4)
    assert isinstance(verdict, JudgeVerdict)
    assert 0.0 <= verdict.relevance_score <= 1.0
    assert verdict.reasoning
    assert verdict.tokens_recalled >= 0
    assert verdict.latency_ms >= 0
