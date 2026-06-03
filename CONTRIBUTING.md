# Contributing to Memor

Thanks for your interest in contributing. This guide covers everything you need to get started.

```
 ┌─────────────────────────────────────────────────────────────┐
 │                  CONTRIBUTION FLOW                           │
 │                                                               │
 │  Fork ──► Branch ──► Code ──► Test ──► PR ──► Review        │
 │                                                               │
 │  Every PR must:                                              │
 │    1. Pass all 45+ existing tests                            │
 │    2. Add tests for new functionality                        │
 │    3. Not regress eval metrics (if touching retrieval)       │
 └─────────────────────────────────────────────────────────────┘
```

## Setup

```bash
# 1. Fork and clone
git clone https://github.com/<your-username>/memor-ai.git
cd memor-ai

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows

# 3. Install all dependencies (dev + local embeddings + anthropic)
pip install -e ".[dev,local,anthropic]"

# 4. Verify everything works
pytest
```

You should see 45 tests passing. If any fail, open an issue before proceeding.

## Project Structure

```
 memor/
 ├── types.py           ← Start here. Core dataclasses.
 ├── interfaces.py      ← Protocols (Embedder, LLM, MemoryStore)
 ├── store/             ← SQLite + sqlite-vec storage
 ├── embed/             ← Embedding backends
 ├── llm/               ← LLM backends
 ├── ingest/            ← Transcript + document parsers
 ├── retrieve/          ← Retrieval engine
 ├── distill/           ← Distillation pipeline
 ├── eval/              ← Eval harness + baselines
 ├── cli.py             ← CLI entry point
 └── daemon.py          ← Background watcher

 tests/                 ← One test_*.py per module
 inspector.py           ← Streamlit UI
 skill/                 ← Claude Code skill
```

## Making Changes

### Branch naming

```
feat/description      # New features
fix/description       # Bug fixes
eval/description      # Eval improvements
docs/description      # Documentation
```

### Commit messages

Follow the existing pattern from git log:

```
feat: <what it does>
fix: <what it fixes>
eval: <what it measures>
docs: <what it documents>
```

One-line summary, imperative mood. Examples from the repo:

```
feat: two-step distillation — extractive pre-filter + abstractive, 82% LLM cost reduction
fix: allow cross-thread sqlite access for streamlit
feat: auto-ingest daemon + agent-readable recall skill
```

### Code style

- **No comments** unless the *why* is non-obvious (hidden constraint, workaround, subtle invariant)
- **No docstrings** on internal functions unless the behavior is surprising
- **Type hints** on function signatures
- **Imports**: `from __future__ import annotations` at the top of every module
- Keep files focused. One responsibility per module.

### Testing

```
 ┌─────────────────────────────────────────────────────────────┐
 │  TESTING RULES                                               │
 │                                                               │
 │  1. Every new module gets a test_<module>.py                 │
 │  2. Use FakeEmbedder for deterministic tests                 │
 │  3. Use tmp_path for database files (no cleanup needed)     │
 │  4. No mocks for the database — hit real SQLite             │
 │  5. If touching retrieval: run eval and report numbers      │
 └─────────────────────────────────────────────────────────────┘
```

```bash
# Run all tests
pytest

# Run a specific test file
pytest tests/test_retriever.py -v

# Run a specific test
pytest tests/test_retriever.py::test_edge_expansion -v
```

The `FakeEmbedder` in `memor/embed/fake.py` generates deterministic vectors from SHA-256 hashes. Use it for all tests — it's fast, reproducible, and doesn't need model downloads.

### Eval-first development

If your change touches retrieval, distillation, or scoring:

1. Run the eval *before* your change: `memor eval cases.json --db test.db`
2. Make your change
3. Run the eval *after*: `memor eval cases.json --db test.db`
4. Include the before/after numbers in your PR description

We don't ship features that can't show a measured delta.

## Areas for Contribution

### Good first issues

- Add a new ingest format (Cursor transcripts, Copilot logs)
- Improve heuristic scoring in `memor/distill/extractive.py`
- Add more signal patterns to the noise filter in `memor/ingest/claude_code.py`
- Write eval cases for edge cases in retrieval

### Intermediate

- Add a new embedding backend (e.g., Cohere, local ONNX)
- Implement incremental re-distillation (update memories when session grows)
- Build an external baseline adapter (see `memor/eval/baselines/`)
- Improve the counterfactual dataset builder labeling

### Advanced

- Multi-project cross-retrieval (transfer patterns across projects)
- Autonomous recall injection (agent-triggered, not skill-triggered)
- Plugin system for custom ingest/embed/LLM backends

## Protocol Interfaces

If you're adding a new backend, implement the corresponding protocol from `memor/interfaces.py`:

```python
# Embedder
class MyEmbedder:
    dim: int = 768
    def embed(self, texts: list[str]) -> list[list[float]]: ...

# LLM
class MyLLM:
    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str: ...

# MemoryStore
class MyStore:
    def add_artifacts(self, artifacts, vectors) -> None: ...
    def add_edge(self, src_id, dst_id, type) -> None: ...
    def search(self, vector, scope, k) -> list[tuple[Artifact, float]]: ...
    def neighbors(self, ids, types, hops=1) -> list[Artifact]: ...
    def deactivate(self, artifact_id, superseded_by) -> None: ...
```

All protocols are `@runtime_checkable`, so `isinstance()` checks work at runtime.

## Pull Request Checklist

- [ ] All existing tests pass (`pytest`)
- [ ] New tests added for new functionality
- [ ] No regressions in eval metrics (if applicable)
- [ ] Commit message follows the `feat:/fix:/eval:/docs:` convention
- [ ] No secrets, API keys, or personal paths in the diff

## Reporting Bugs

Open an issue with:

1. What you did (commands run, config used)
2. What you expected
3. What actually happened
4. Your environment (Python version, OS, Node version)

## Code of Conduct

Be kind, be constructive, be specific. We're building tools for developers — act like one.
