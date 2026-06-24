# Changelog

All notable changes to this project will be documented in this file.

## [0.9.0] - 2026-06-24

### Fixed
- **Cursor agent detection** — Cursor fires a `beforeSubmitPrompt` hook whose payload carries `model`/`cursor_version` and `workspace_roots` (not `cwd`), so every Cursor call was mislabeled `codex` and scoped to `unknown`. Now detected as its own `cursor` agent and scoped via `workspace_roots`, so recalled memories inject into Cursor correctly. (#34)
- **Dashboard** — distinct `cursor` agent badge and breakdown-card color. (#34, #36)

### Changed
- **Retrieval performance** — batched memory quality-score lookup and a KNN-fetch cap, reducing recall latency with no behavior change. (#35)
- **Eval** — optional `temperature` on `OpenAICompatLLM` (default unchanged for production) enables a deterministic, repeatable counterfactual judge. (#36)

### Docs
- Closed the write-side distillation-quality research arc: every hypothesis came back sub-resolution or washed on the temp=0 paired counterfactual — recall is at its practical ceiling on this corpus. (#36)

## [0.1.0] - 2026-06-03

### Added

**Core**
- SQLite + sqlite-vec store with artifact storage, vector search (HNSW, cosine), edge traversal (recursive CTE), and supersede/deactivate
- Pluggable protocols: `Embedder`, `LLM`, `MemoryStore` (all `@runtime_checkable`)
- Core types: `Artifact`, `Scope`, `Hit`, `RetrievalTrace`

**Ingest**
- Claude Code JSONL transcript parser with noise filtering (regex filler detection, skill boilerplate removal, token threshold)
- Markdown/research document parser (splits on headings)
- Recursive project ingestion (`ingest-project` command)

**Retrieval**
- Scope-filtered vector search with recency blending (configurable weight)
- 1-hop edge expansion via recursive CTE
- Full retrieval trace with per-hit score breakdown (sim, recency, edge components)

**Distillation**
- Two-step pipeline: extractive pre-filter (free, local) + LLM abstractive
- Extractive step: TF-IDF scoring + embedding k-means clustering + heuristic signal detection
- 82% reduction in LLM input tokens measured on real data
- Dedup via embedding similarity (0.92 threshold)
- Contradiction handling via supersede (deactivate stale memories)
- LLM-free fallback: `ExtractiveDistiller` stores key chunks directly as memories

**Eval**
- Built-in harness with 4 baselines: no-memory, last-N, naive-RAG, memory (full pipeline)
- Metrics: recall@k, nDCG@k, tokens_sent, token_savings, latency (p50 + p95)
- Edge expansion ablation test
- Contradiction/supersede evaluation
- Counterfactual auto-labeling from real transcripts
- External baseline adapter stubs: Graphiti, claude-mem

**Daemon**
- Auto-ingest: polls `~/.claude/projects/` every 30s for new/modified transcripts
- Auto-distill: extractive fallback when no API key, two-step when LLM available
- State persistence: `ingested.json` + `distilled.json`

**CLI**
- `daemon`, `ingest-project`, `ingest-cc`, `ingest-doc`, `distill`, `query`, `eval`, `build-cases`, `inspector`, `setup`
- npm/bun installable via `memor-ai` package with auto Python venv setup

**Inspector**
- Streamlit UI with 4 tabs: Query (retrieval inspector), Browse (artifact browser), Eval (run + view), Edges (relationship explorer)

**Skill**
- Claude Code recall skill with agent-readable output format

**Embeddings**
- Local: sentence-transformers `bge-small-en-v1.5` (384-dim, offline, free)
- API: OpenAI-compatible embedding endpoint
- Fake: deterministic SHA-256 based (for tests)

**Testing**
- 45 tests across 17 test files
- Covers: types, interfaces, embedders, store, ingest, retrieval, distillation, eval, CLI, skill, daemon
