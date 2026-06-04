# Phase 1: Honest Metrics + TUI Inspector — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make memor's claims provable (accurate token counting, LLM-as-judge eval, embedding benchmarks) and replace the Streamlit inspector with a professional-grade terminal UI.

**Architecture:** Four independent workstreams that share the existing module structure. Token counting is foundational (changes propagate to eval metrics). LLM-as-judge builds on the existing eval runner. Embedding benchmark builds on the existing embed + eval layers. TUI replaces inspector.py entirely but talks to the same SqliteStore/Retriever.

**Tech Stack:** tiktoken (token counting), textual (TUI framework), existing memor eval/embed/store infrastructure.

---

## File Structure

### New files
- `memor/tokencount.py` — central token counting utility (wraps tiktoken)
- `memor/eval/judge.py` — LLM-as-judge eval: hold-out sessions, score recalled context
- `memor/eval/embed_benchmark.py` — embedding model benchmark runner
- `memor/tui/__init__.py` — TUI package
- `memor/tui/app.py` — main Textual app with tab screens
- `memor/tui/screens/__init__.py` — screens package
- `memor/tui/screens/query.py` — retrieval inspector screen
- `memor/tui/screens/browse.py` — artifact browser screen
- `memor/tui/screens/eval_screen.py` — eval results screen
- `memor/tui/screens/edges.py` — edge explorer screen
- `tests/test_tokencount.py` — token counting tests
- `tests/test_judge.py` — LLM-as-judge tests
- `tests/test_embed_benchmark.py` — embedding benchmark tests
- `tests/test_tui.py` — TUI app tests (headless via textual pilot)

### Modified files
- `memor/ingest/claude_code.py:50` — replace `len(text) // 4` with `count_tokens(text)`
- `memor/distill/distiller.py:25` — replace `len(text) // 4` with `count_tokens(text)`
- `memor/cli.py` — add `inspector` command that launches TUI, add `bench-embed` command
- `pyproject.toml` — add tiktoken + textual to dependencies
- `package.json` — add textual to postinstall deps

---

## Task 1: Accurate Token Counting with tiktoken

**Files:**
- Create: `memor/tokencount.py`
- Create: `tests/test_tokencount.py`
- Modify: `memor/ingest/claude_code.py:50`
- Modify: `memor/distill/distiller.py:25`
- Modify: `pyproject.toml:5`

- [ ] **Step 1: Write failing tests for token counting**

Create `tests/test_tokencount.py`:

```python
from memor.tokencount import count_tokens

def test_count_tokens_english():
    text = "We decided to use argon2 for password hashing."
    result = count_tokens(text)
    assert isinstance(result, int)
    assert result > 0
    assert result != len(text) // 4  # should differ from naive estimate

def test_count_tokens_code():
    text = 'def authenticate(user: str, password: str) -> bool:\n    return bcrypt.checkpw(password, user.hash)'
    result = count_tokens(text)
    assert isinstance(result, int)
    assert result > 0

def test_count_tokens_empty():
    assert count_tokens("") == 0

def test_count_tokens_short():
    assert count_tokens("hi") >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tokencount.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memor.tokencount'`

- [ ] **Step 3: Add tiktoken dependency**

In `pyproject.toml`, add `"tiktoken>=0.7"` to the `dependencies` list:

```toml
dependencies = [
  "sqlite-vec>=0.1.6",
  "numpy>=1.26",
  "typer>=0.12",
  "httpx>=0.27",
  "tiktoken>=0.7",
]
```

Then install: `pip install -e ".[dev]"`

- [ ] **Step 4: Implement count_tokens**

Create `memor/tokencount.py`:

```python
from __future__ import annotations
import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_enc.encode(text))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_tokencount.py -v`
Expected: All 4 PASS

- [ ] **Step 6: Replace naive token counting in ingest**

In `memor/ingest/claude_code.py`, line 50, change:

```python
token_count = max(1, len(text) // 4)
```

to:

```python
from memor.tokencount import count_tokens
# (put import at top of file, not inline)
token_count = max(1, count_tokens(text))
```

Add the import at the top of the file (after the existing imports around line 5):

```python
from memor.tokencount import count_tokens
```

- [ ] **Step 7: Replace naive token counting in distiller**

In `memor/distill/distiller.py`, line 25, change:

```python
token_count=max(1, len(text) // 4),
```

to:

```python
token_count=max(1, count_tokens(text)),
```

Add the import at the top of the file (after the existing imports):

```python
from memor.tokencount import count_tokens
```

- [ ] **Step 8: Run full test suite to verify no regressions**

Run: `pytest -q`
Expected: All 45+ tests pass. Some token_count values in existing tests may change since they used `len(text)//4` or hardcoded values — if any fail, update the test fixtures to use `count_tokens()` or loosen assertions to `assert result.token_count > 0`.

- [ ] **Step 9: Commit**

```bash
git add memor/tokencount.py tests/test_tokencount.py memor/ingest/claude_code.py memor/distill/distiller.py pyproject.toml
git commit -m "feat: replace naive len//4 token counting with tiktoken cl100k_base"
```

---

## Task 2: LLM-as-Judge Eval

**Files:**
- Create: `memor/eval/judge.py`
- Create: `tests/test_judge.py`
- Modify: `memor/cli.py` (add `eval-judge` command)

**Context:** The current eval (`build_counterfactual_cases`) defines "relevant" as chunks sharing 4+ letter words — lexical overlap, not semantic relevance. The LLM-as-judge approach: hold out the last N turns of a session, retrieve context from prior sessions, and ask an LLM "given this recalled context, how well does it prepare you for the task that actually happened?" This measures whether retrieval is *useful*, not just lexically similar.

- [ ] **Step 1: Write failing tests for judge eval**

Create `tests/test_judge.py`:

```python
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
    """Sessions with >= 4 turns produce a judge case: opening as query, last N as holdout."""
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
    assert case.query  # opening turn of session s2
    assert len(case.holdout_texts) == 2  # last 2 turns
    assert case.scope_project == "p"


def test_build_judge_cases_skips_short_sessions():
    """Sessions with < 4 turns are skipped (not enough to hold out)."""
    chunks = [
        _chunk("s1", 0, "hello", 100),
        _chunk("s1", 1, "hi there", 101),
        _chunk("s2", 0, "how do I fix auth", 200),
        _chunk("s2", 1, "check the refresh token logic", 201),
    ]
    cases = build_judge_cases(chunks, project="p", holdout_turns=2)
    assert len(cases) == 0


def test_run_judge_case_with_fake_llm(tmp_path):
    """run_judge_case retrieves context, formats a judge prompt, and parses the verdict."""
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
                "reasoning": "The recalled context mentions the users table schema which is relevant to adding auth."
            })

    verdict = run_judge_case(case, store=s, embedder=e, llm=FakeLLM(), k=4)
    assert isinstance(verdict, JudgeVerdict)
    assert 0.0 <= verdict.relevance_score <= 1.0
    assert verdict.reasoning
    assert verdict.tokens_recalled >= 0
    assert verdict.latency_ms >= 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_judge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memor.eval.judge'`

- [ ] **Step 3: Implement judge eval**

Create `memor/eval/judge.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_judge.py -v`
Expected: All 3 PASS

- [ ] **Step 5: Add CLI command for judge eval**

In `memor/cli.py`, add after the existing `eval` command (around line 52):

```python
@app.command("eval-judge")
def eval_judge_cmd(project: str = typer.Option(...), db: str = "memor.db",
                   k: int = 8, fake: bool = False,
                   llm_provider: str = "anthropic", llm_model: str = "claude-sonnet-4-6",
                   holdout: int = 2):
    """Run LLM-as-judge eval: measures whether recalled context is actually useful."""
    from memor.eval.judge import build_judge_cases, run_judge_suite
    e = _embedder(fake); s = SqliteStore(db, dim=e.dim)
    rows = s.db.execute("SELECT * FROM artifacts WHERE project=? AND kind='session_chunk'",
                        (project,)).fetchall()
    arts = [s._row_to_artifact(r) for r in rows]
    cases = build_judge_cases(arts, project=project, holdout_turns=holdout)
    if not cases:
        typer.echo("No judge cases could be built — need at least 2 sessions with >= 4 turns each.")
        raise typer.Exit(1)
    typer.echo(f"Built {len(cases)} judge cases. Running evaluation...")
    if llm_provider == "anthropic":
        from memor.llm.anthropic import AnthropicLLM
        llm = AnthropicLLM(model=llm_model)
    else:
        from memor.llm.openai_compat import OpenAICompatLLM
        import os
        llm = OpenAICompatLLM(base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1"),
                              api_key=os.environ.get("OPENAI_API_KEY", ""), model=llm_model)
    summary = run_judge_suite(cases, store=s, embedder=e, llm=llm, k=k)
    typer.echo(json.dumps(summary, indent=2))
    s.save_eval_run({"type": "judge", "k": k, "project": project, "holdout": holdout}, summary)
    typer.echo(f"Judge eval complete: mean relevance = {summary['mean_relevance']:.3f}")
```

- [ ] **Step 6: Run full test suite**

Run: `pytest -q`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add memor/eval/judge.py tests/test_judge.py memor/cli.py
git commit -m "feat: add LLM-as-judge eval — measures if recalled context is actually useful"
```

---

## Task 3: Embedding Model Benchmark

**Files:**
- Create: `memor/eval/embed_benchmark.py`
- Create: `tests/test_embed_benchmark.py`
- Modify: `memor/cli.py` (add `bench-embed` command)

**Context:** The current system uses bge-small-en-v1.5 (384-dim) for all embeddings. Code-heavy sessions might benefit from different models. This benchmark runs the eval suite with multiple embedding models and compares recall@k, nDCG@k, and latency.

- [ ] **Step 1: Write failing tests for embedding benchmark**

Create `tests/test_embed_benchmark.py`:

```python
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
    """Benchmark runner works with a custom embedder factory (FakeEmbedder for testing)."""
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_embed_benchmark.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memor.eval.embed_benchmark'`

- [ ] **Step 3: Implement embedding benchmark**

Create `memor/eval/embed_benchmark.py`:

```python
from __future__ import annotations
import time
from dataclasses import dataclass
from memor.store.sqlite_store import SqliteStore
from memor.eval.runner import run_suite
from memor.eval.dataset import EvalCase
from memor.types import Artifact

CANDIDATE_MODELS = [
    {"name": "bge-small-en-v1.5", "model_name": "BAAI/bge-small-en-v1.5"},
    {"name": "all-MiniLM-L6-v2", "model_name": "sentence-transformers/all-MiniLM-L6-v2"},
    {"name": "nomic-embed-text-v1.5", "model_name": "nomic-ai/nomic-embed-text-v1.5"},
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_embed_benchmark.py -v`
Expected: All 2 PASS

- [ ] **Step 5: Add CLI command for embedding benchmark**

In `memor/cli.py`, add after the `eval-judge` command:

```python
@app.command("bench-embed")
def bench_embed(project: str = typer.Option(...), db: str = "memor.db",
                k: int = 8, fake: bool = False):
    """Benchmark multiple embedding models on your data. Compares recall@k, nDCG@k, and latency."""
    from memor.eval.embed_benchmark import run_embed_benchmark, CANDIDATE_MODELS
    from memor.eval.dataset import build_counterfactual_cases
    e = _embedder(fake); s = SqliteStore(db, dim=e.dim)
    rows = s.db.execute("SELECT * FROM artifacts WHERE project=? AND kind='session_chunk'",
                        (project,)).fetchall()
    arts = [s._row_to_artifact(r) for r in rows]
    cases = build_counterfactual_cases(arts, project=project)
    if not cases:
        typer.echo("No eval cases could be built.")
        raise typer.Exit(1)
    typer.echo(f"Running benchmark on {len(arts)} artifacts, {len(cases)} cases...")
    if fake:
        from memor.embed.fake import FakeEmbedder
        results = run_embed_benchmark(arts, cases, model_specs=[{"name":"fake","model_name":"fake"}],
                                      embedder_factory=lambda _: FakeEmbedder(dim=16),
                                      db_dir=str(Path(db).parent), k=k)
    else:
        results = run_embed_benchmark(arts, cases, db_dir=str(Path(db).parent), k=k)
    typer.echo(f"\n{'Model':<30} {'Dim':>5} {'Recall@k':>10} {'nDCG@k':>10} {'Embed ms':>10} {'Query ms':>10}")
    typer.echo("-" * 80)
    for r in results:
        typer.echo(f"{r.model_name:<30} {r.dim:>5} {r.recall_at_k:>10.3f} {r.ndcg_at_k:>10.3f} "
                   f"{r.embed_latency_ms:>10.1f} {r.retrieval_latency_ms:>10.1f}")
```

- [ ] **Step 6: Run full test suite**

Run: `pytest -q`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add memor/eval/embed_benchmark.py tests/test_embed_benchmark.py memor/cli.py
git commit -m "feat: add embedding model benchmark — compare recall across embedding models"
```

---

## Task 4: TUI Inspector — App Shell + Query Screen

**Files:**
- Create: `memor/tui/__init__.py`
- Create: `memor/tui/app.py`
- Create: `memor/tui/screens/__init__.py`
- Create: `memor/tui/screens/query.py`
- Create: `tests/test_tui.py`
- Modify: `pyproject.toml` (add textual dependency)

**Context:** Textual is a Python TUI framework. Apps are built from Screen/Widget classes. We use the `TabbedContent` widget for tab navigation. Each screen is a separate module. The app loads the SqliteStore on startup and passes it to screens.

- [ ] **Step 1: Add textual dependency**

In `pyproject.toml`, add `"textual>=0.80"` to the dependencies list:

```toml
dependencies = [
  "sqlite-vec>=0.1.6",
  "numpy>=1.26",
  "typer>=0.12",
  "httpx>=0.27",
  "tiktoken>=0.7",
  "textual>=0.80",
]
```

Then install: `pip install -e ".[dev]"`

- [ ] **Step 2: Write failing test for TUI app**

Create `tests/test_tui.py`:

```python
import pytest
from memor.tui.app import MemorApp


@pytest.fixture
def app_fixture(tmp_path):
    from memor.store.sqlite_store import SqliteStore
    from memor.embed.fake import FakeEmbedder
    from memor.types import Artifact

    db_path = str(tmp_path / "test.db")
    e = FakeEmbedder(dim=16)
    s = SqliteStore(db_path, dim=16)
    arts = [
        Artifact(id="a1", kind="session_chunk", project="p", source="cc",
                 text="auth refresh token loop fix", token_count=6, created_at=100.0,
                 meta={"session_id": "s1", "role": "assistant", "ord": 0}),
        Artifact(id="a2", kind="memory", project="p", source="distill",
                 text="Use argon2 for password hashing", token_count=6, created_at=200.0,
                 meta={"session_id": "s1", "mem_type": "decision"}),
    ]
    s.add_artifacts(arts, e.embed([a.text for a in arts]))
    return MemorApp(db_path=db_path, fake=True)


@pytest.mark.asyncio
async def test_app_starts_and_has_tabs(app_fixture):
    async with app_fixture.run_test() as pilot:
        app = pilot.app
        assert app.title == "Memor Inspector"
        tabs = app.query("TabbedContent")
        assert len(tabs) == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_tui.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memor.tui'`

- [ ] **Step 4: Create TUI package and app shell**

Create `memor/tui/__init__.py`:

```python
```

Create `memor/tui/screens/__init__.py`:

```python
```

Create `memor/tui/app.py`:

```python
from __future__ import annotations
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, TabbedContent, TabPane
from memor.store.sqlite_store import SqliteStore
from memor.tui.screens.query import QueryScreen
from memor.tui.screens.browse import BrowseScreen
from memor.tui.screens.eval_screen import EvalScreen
from memor.tui.screens.edges import EdgesScreen


class MemorApp(App):
    CSS = """
    Screen { layout: vertical; }
    TabbedContent { height: 1fr; }
    """
    TITLE = "Memor Inspector"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("1", "tab_query", "Query"),
        ("2", "tab_browse", "Browse"),
        ("3", "tab_eval", "Eval"),
        ("4", "tab_edges", "Edges"),
    ]

    def __init__(self, db_path: str, fake: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.db_path = db_path
        self.fake = fake
        self._store = None
        self._embedder = None

    @property
    def store(self) -> SqliteStore:
        if self._store is None:
            self._store = SqliteStore(self.db_path, dim=self.embedder.dim)
        return self._store

    @property
    def embedder(self):
        if self._embedder is None:
            if self.fake:
                from memor.embed.fake import FakeEmbedder
                self._embedder = FakeEmbedder(dim=16)
            else:
                from memor.embed.local import LocalEmbedder
                self._embedder = LocalEmbedder()
        return self._embedder

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent("Query", "Browse", "Eval", "Edges"):
            with TabPane("Query", id="tab-query"):
                yield QueryScreen(self.store, self.embedder)
            with TabPane("Browse", id="tab-browse"):
                yield BrowseScreen(self.store)
            with TabPane("Eval", id="tab-eval"):
                yield EvalScreen(self.store)
            with TabPane("Edges", id="tab-edges"):
                yield EdgesScreen(self.store)
        yield Footer()

    def action_tab_query(self):
        self.query_one(TabbedContent).active = "tab-query"

    def action_tab_browse(self):
        self.query_one(TabbedContent).active = "tab-browse"

    def action_tab_eval(self):
        self.query_one(TabbedContent).active = "tab-eval"

    def action_tab_edges(self):
        self.query_one(TabbedContent).active = "tab-edges"
```

- [ ] **Step 5: Create the Query screen**

Create `memor/tui/screens/query.py`:

```python
from __future__ import annotations
from textual.app import ComposeResult
from textual.widgets import Static, Input, DataTable, Label
from textual.containers import Vertical, Horizontal
from textual.widget import Widget
from memor.retrieve.retriever import Retriever
from memor.types import Scope


class QueryScreen(Widget):
    DEFAULT_CSS = """
    QueryScreen { height: 1fr; layout: vertical; }
    #query-input { dock: top; margin: 1 2; }
    #query-stats { dock: top; margin: 0 2; height: 1; }
    #query-results { margin: 1 2; height: 1fr; }
    """

    def __init__(self, store, embedder, **kwargs):
        super().__init__(**kwargs)
        self.store = store
        self.embedder = embedder

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Query: what did we decide about auth?", id="query-input")
        yield Label("", id="query-stats")
        yield DataTable(id="query-results")

    def on_mount(self):
        table = self.query_one("#query-results", DataTable)
        table.add_columns("Rank", "Score", "Kind", "Project", "Text")
        table.cursor_type = "row"

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id != "query-input":
            return
        query_text = event.value.strip()
        if not query_text:
            return
        r = Retriever(self.store, self.embedder, k=8, recency_weight=0.2, edge_expand=True)
        trace = r.query(query_text, Scope())
        table = self.query_one("#query-results", DataTable)
        table.clear()
        for i, h in enumerate(trace.hits, 1):
            a = h.artifact
            text_preview = a.text[:120].replace("\n", " ")
            table.add_row(str(i), f"{h.score:.3f}", a.kind, a.project or "-", text_preview)
        tokens = sum(h.artifact.token_count for h in trace.hits)
        self.query_one("#query-stats", Label).update(
            f"{len(trace.hits)} hits | {trace.latency_ms:.1f}ms | {tokens} tokens"
        )
```

- [ ] **Step 6: Create placeholder screens for Browse, Eval, Edges**

Create `memor/tui/screens/browse.py`:

```python
from __future__ import annotations
import json
from textual.app import ComposeResult
from textual.widgets import Static, DataTable, Input, Label, Select
from textual.containers import Vertical, Horizontal
from textual.widget import Widget


class BrowseScreen(Widget):
    DEFAULT_CSS = """
    BrowseScreen { height: 1fr; layout: vertical; }
    #browse-filters { dock: top; height: 3; layout: horizontal; margin: 1 2; }
    #browse-filters Input { width: 1fr; }
    #browse-filters Select { width: 20; }
    #browse-stats { margin: 0 2; height: 1; }
    #browse-table { margin: 1 2; height: 1fr; }
    """

    def __init__(self, store, **kwargs):
        super().__init__(**kwargs)
        self.store = store
        self.page = 0
        self.page_size = 50

    def compose(self) -> ComposeResult:
        projects = [r[0] for r in self.store.db.execute(
            "SELECT DISTINCT project FROM artifacts").fetchall()]
        kinds = [r[0] for r in self.store.db.execute(
            "SELECT DISTINCT kind FROM artifacts").fetchall()]
        with Horizontal(id="browse-filters"):
            yield Select(
                [(p, p) for p in ["all"] + projects],
                value="all", id="browse-project",
            )
            yield Select(
                [(k, k) for k in ["all"] + kinds],
                value="all", id="browse-kind",
            )
            yield Input(placeholder="Search text...", id="browse-search")
        yield Label("", id="browse-stats")
        yield DataTable(id="browse-table")

    def on_mount(self):
        table = self.query_one("#browse-table", DataTable)
        table.add_columns("ID", "Kind", "Project", "Tokens", "Text")
        table.cursor_type = "row"
        self._refresh_data()

    def on_select_changed(self, event):
        self._refresh_data()

    def on_input_submitted(self, event):
        if event.input.id == "browse-search":
            self._refresh_data()

    def _refresh_data(self):
        where = ["active = 1"]
        params = []
        try:
            project = self.query_one("#browse-project", Select).value
            if project != "all" and project is not Select.BLANK:
                where.append("project = ?"); params.append(project)
        except Exception:
            pass
        try:
            kind = self.query_one("#browse-kind", Select).value
            if kind != "all" and kind is not Select.BLANK:
                where.append("kind = ?"); params.append(kind)
        except Exception:
            pass
        try:
            search = self.query_one("#browse-search", Input).value
            if search:
                where.append("text LIKE ?"); params.append(f"%{search}%")
        except Exception:
            pass

        count = self.store.db.execute(
            f"SELECT COUNT(*) FROM artifacts WHERE {' AND '.join(where)}", params
        ).fetchone()[0]
        self.query_one("#browse-stats", Label).update(f"{count} artifacts")

        rows = self.store.db.execute(
            f"SELECT * FROM artifacts WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [self.page_size, self.page * self.page_size],
        ).fetchall()
        table = self.query_one("#browse-table", DataTable)
        table.clear()
        for r in rows:
            text_preview = (r["text"] or "")[:100].replace("\n", " ")
            table.add_row(r["id"][:20], r["kind"], r["project"] or "-",
                          str(r["token_count"]), text_preview)
```

Create `memor/tui/screens/eval_screen.py`:

```python
from __future__ import annotations
import json
from textual.app import ComposeResult
from textual.widgets import Static, DataTable, Label
from textual.widget import Widget


class EvalScreen(Widget):
    DEFAULT_CSS = """
    EvalScreen { height: 1fr; layout: vertical; }
    #eval-stats { margin: 1 2; height: 1; }
    #eval-table { margin: 1 2; height: 1fr; }
    """

    def __init__(self, store, **kwargs):
        super().__init__(**kwargs)
        self.store = store

    def compose(self) -> ComposeResult:
        yield Label("", id="eval-stats")
        yield DataTable(id="eval-table")

    def on_mount(self):
        table = self.query_one("#eval-table", DataTable)
        table.add_columns("Run", "Type", "k", "Project", "Recall@k", "nDCG@k", "Token Savings")
        table.cursor_type = "row"
        self._load_runs()

    def _load_runs(self):
        try:
            runs = self.store.db.execute(
                "SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        except Exception:
            runs = []
        table = self.query_one("#eval-table", DataTable)
        table.clear()
        if not runs:
            self.query_one("#eval-stats", Label).update(
                "No eval runs yet. Run: memor eval <cases.json> or memor eval-judge --project <name>"
            )
            return
        self.query_one("#eval-stats", Label).update(f"{len(runs)} eval runs")
        for r in runs:
            config = json.loads(r["config"])
            metrics = json.loads(r["metrics"])
            eval_type = config.get("type", "counterfactual")
            k = str(config.get("k", "?"))
            project = config.get("project", "-")
            mem = metrics.get("memory", metrics)
            recall = f"{mem.get('recall@k', mem.get('mean_relevance', 0)):.3f}"
            ndcg = f"{mem.get('ndcg@k', 0):.3f}" if "ndcg@k" in mem else "-"
            savings = f"{mem.get('token_savings_vs_full', 0):.1%}" if "token_savings_vs_full" in mem else "-"
            table.add_row(str(r["id"]), eval_type, k, project, recall, ndcg, savings)
```

Create `memor/tui/screens/edges.py`:

```python
from __future__ import annotations
from textual.app import ComposeResult
from textual.widgets import Static, Input, DataTable, Label
from textual.widget import Widget


class EdgesScreen(Widget):
    DEFAULT_CSS = """
    EdgesScreen { height: 1fr; layout: vertical; }
    #edge-input { dock: top; margin: 1 2; }
    #edge-stats { margin: 0 2; height: 1; }
    #edge-table { margin: 1 2; height: 1fr; }
    """

    def __init__(self, store, **kwargs):
        super().__init__(**kwargs)
        self.store = store

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Artifact ID (e.g. mem:s1:abc123)", id="edge-input")
        yield Label("", id="edge-stats")
        yield DataTable(id="edge-table")

    def on_mount(self):
        table = self.query_one("#edge-table", DataTable)
        table.add_columns("Direction", "Type", "Artifact ID", "Text")
        table.cursor_type = "row"
        edge_count = self.store.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        by_type = self.store.db.execute(
            "SELECT type, COUNT(*) FROM edges GROUP BY type"
        ).fetchall()
        summary = ", ".join(f"{t}: {c}" for t, c in by_type) if by_type else "none"
        self.query_one("#edge-stats", Label).update(
            f"{edge_count} total edges ({summary})"
        )

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id != "edge-input":
            return
        artifact_id = event.value.strip()
        if not artifact_id:
            return
        table = self.query_one("#edge-table", DataTable)
        table.clear()
        outgoing = self.store.db.execute(
            "SELECT e.type, a.id, a.text FROM edges e JOIN artifacts a ON e.dst_id = a.id WHERE e.src_id = ?",
            (artifact_id,),
        ).fetchall()
        incoming = self.store.db.execute(
            "SELECT e.type, a.id, a.text FROM edges e JOIN artifacts a ON e.src_id = a.id WHERE e.dst_id = ?",
            (artifact_id,),
        ).fetchall()
        for t, aid, txt in outgoing:
            table.add_row("->", t, aid[:30], (txt or "")[:80].replace("\n", " "))
        for t, aid, txt in incoming:
            table.add_row("<-", t, aid[:30], (txt or "")[:80].replace("\n", " "))
        if not outgoing and not incoming:
            self.query_one("#edge-stats", Label).update("No edges for this artifact")
```

- [ ] **Step 7: Run the TUI test**

Run: `pytest tests/test_tui.py -v`
Expected: PASS — app starts, has TabbedContent with 4 tabs

- [ ] **Step 8: Wire up the CLI `inspector` command**

In `memor/cli.py`, add a new command:

```python
@app.command("inspector")
def inspector_cmd(db: str = "memor.db", fake: bool = False):
    """Launch the TUI inspector."""
    from memor.tui.app import MemorApp
    tui = MemorApp(db_path=db, fake=fake)
    tui.run()
```

- [ ] **Step 9: Run full test suite**

Run: `pytest -q`
Expected: All tests pass

- [ ] **Step 10: Commit**

```bash
git add memor/tui/ tests/test_tui.py memor/cli.py pyproject.toml
git commit -m "feat: add textual TUI inspector — replaces Streamlit with terminal-native UI"
```

---

## Task 5: Update bin/memor.mjs to route `inspector` to TUI

**Files:**
- Modify: `bin/memor.mjs`

**Context:** Currently `bin/memor.mjs` routes `inspector` to `streamlit run inspector.py`. We need it to route to `python -m memor.cli inspector` instead.

- [ ] **Step 1: Update the inspector routing in memor.mjs**

In `bin/memor.mjs`, find the section that handles the `inspector` command and change it from spawning streamlit to delegating to the CLI:

Find the block that looks like:

```javascript
if (args[0] === 'inspector') {
  // ... spawns streamlit ...
}
```

Replace it so `inspector` is no longer a special case — it just falls through to the default `python -m memor.cli` handler like every other command. Remove the streamlit-specific spawn code.

- [ ] **Step 2: Update package.json if streamlit is mentioned in postinstall**

Check `scripts/postinstall.mjs` for any streamlit installation. If it installs streamlit as a dependency, leave it for now (old inspector.py still exists as a fallback). Optionally add textual to the pip install list in postinstall.

In `scripts/postinstall.mjs`, find where pip packages are installed and add `textual`:

```javascript
// In the pip install command, add textual to the list
```

- [ ] **Step 3: Test the inspector command end-to-end**

Run: `node bin/memor.mjs inspector --db memor.db --fake`
Expected: TUI launches in the terminal

- [ ] **Step 4: Commit**

```bash
git add bin/memor.mjs scripts/postinstall.mjs
git commit -m "feat: route inspector command to TUI instead of Streamlit"
```

---

## Task 6: Final Integration — Run All Tests, Verify Dependencies

**Files:**
- Modify: `pyproject.toml` (verify all new deps)
- Modify: `package.json` (update files field if needed)

- [ ] **Step 1: Verify pyproject.toml has all new dependencies**

Ensure `pyproject.toml` dependencies list includes:

```toml
dependencies = [
  "sqlite-vec>=0.1.6",
  "numpy>=1.26",
  "typer>=0.12",
  "httpx>=0.27",
  "tiktoken>=0.7",
  "textual>=0.80",
]
```

And add `pytest-asyncio` to dev deps for the TUI test:

```toml
dev = ["pytest>=8.0", "pytest-asyncio>=0.23"]
```

- [ ] **Step 2: Reinstall and run full test suite**

```bash
pip install -e ".[dev]"
pytest -v
```

Expected: All tests pass (both old and new)

- [ ] **Step 3: Verify new CLI commands exist**

```bash
python -m memor.cli --help
```

Expected output includes: `eval-judge`, `bench-embed`, `inspector`

- [ ] **Step 4: Commit any final fixups**

```bash
git add -A
git commit -m "chore: finalize phase 1 — all deps, tests, CLI commands wired"
```
