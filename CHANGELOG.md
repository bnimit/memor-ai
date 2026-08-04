# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.13.0] - 2026-08-04

### Fixed
- **Source code was being silently destroyed.** `detect_content_type` classified source as `log`, and the log crusher deletes lines it judges repetitive. Reading `memor/service.py` through the compressor returned 580 of 3,977 tokens with 399 lines gone; `index.html` lost 1,303 lines (97%). The trigger is cheap — `var(--warn)` in CSS matches `\bWARN\b`, and three such lines classify a whole file. This was live in the proxy path, so an agent could receive a mutilated file and edit against content that was never in it. Source is now its own content type and passes through untouched, and truncation markers report the real number of omitted lines instead of claiming "dropped repetitive INFO/DEBUG lines" over a Python file.
- **Content type is now read from the filename** before guessing from bytes. The agent's tool call already carries `input.file_path`; the pipeline discarded it and had no option but to sniff. Sniffing is now reserved for payloads with no file behind them — shell output, grep results, API responses.

### Added
- **Code-aware compression.** AST-boundary skeletonization keeps imports, signatures, decorators, docstrings and module/class-level assignments, eliding only function bodies. The result is re-parsed before return and discarded if it does not parse. Python via stdlib `ast`; Go, TypeScript, TSX, JavaScript and Rust via the optional `code` extra (~4.8 MB, degrades to passthrough without it). Measured: Python 63.7% over 74 files, Go 66% / TypeScript 26% over 398 files, zero parse failures.
- **Compression of older tool payloads** (`compress_older_turns`, default off). The newest read of each file stays byte-exact; stale reads are skeletonized. 78.5% of code payload is re-reads, so the rule reaches most of it.
- **Tool-result provenance** — `extract_all_tool_payloads` correlates `tool_result.tool_use_id` back to the originating `tool_use`, recovering tool name, file path, and per-file recency.
- **Episode-level recall accounting** — `memor recall-worth` and `/api/recall-worth`. An episode is one user prompt through every tool call it triggers. Result on 4,078 episodes: no measurable effect, reported as such rather than hidden.
- **Realized-savings and cost reporting** — `memor compression-worth` reads the ledger; `memor cost-compare` reads the provider's own token usage from transcripts, so prompt-cache re-formation is counted rather than invisible. Both refuse to report an effect unless the direction survives stratification.
- **Compression panel on the dashboard**, leading with whether the feature is actually running. A flag can be true while the installed build predates it; `liveness()` compares intent against the ledger and says so.
- Test-runner output (pytest, go test, jest) and nested JSON arrays now compress; both previously fell through at 0%.

### Changed
- CCR ids are content-addressed (`blake2b`) rather than `uuid4()`, so rewriting a payload that recurs across requests no longer changes the prompt prefix every call and misses the cache every time.

### Removed
- **Cursor subscription wire MITM** — the mitmproxy addon, `cursor-wire-*` commands, `ai.memor.cursor-wire` service unit, `cursor_wire` config keys, and the dashboard Cursor Wire chip are gone. Measurement showed Cursor's Composer traffic never reaches a local proxy (both the Node and Chromium network stacks were covered; only control-plane RPCs appeared), and the exchange that is actually billed — Cursor's servers to the model — never touches the user's machine, so no local savings figure could be verified. Compression for Cursor now runs entirely through the Shell compress hooks, which crush tool output before Cursor ingests it.
- `memor uninstall-proxy --agent cursor` and `memor service uninstall` clean up any leftover wire settings, service unit, and config keys from 0.12.0.

## [0.12.0] - 2026-08-02

### Added
- **Cursor install automation** — `memor install-proxy --agent cursor` enables memory hooks, Shell compress hooks, and the BYOK proxy on `:8421`. Flag: `--yes`.
- **Agent desk dashboard** — Overview plus per-agent panes (Claude, Cursor, Codex, …) with fintech-style cumulative savings equity curves, recall volume charts, and `/api/agent-desk`.

### Changed
- Dashboard layout de-densified: status chips + portfolio KPIs on Overview; deep tables stay on Overview; each agent desk shows focused KPIs and filtered recalls.

## [0.11.0] - 2026-08-01

### Added
- **Goose + Kimi proxy install** — `memor install-proxy --agent goose|kimi` rewrites provider config, stamps `x-agent` headers, and registers per-agent upstream routing. Goose Desktop providers registered only in `config.yaml` (no `custom_providers/*.json`) are materialized automatically for known providers (`custom_deepseek`, etc.) or via `--upstream-url`.
- **Cursor + Cline + OpenCode proxy install** — `memor install-proxy --agent cursor|cline|opencode` for BYOK / settings-file agents. Cursor and Cline use path-prefixed proxy URLs (`/cursor/v1`, `/cline/v1`) for ledger attribution; OpenCode rewrites `opencode.json` provider `baseURL`.
- **Cursor Shell compression hooks** — `memor install-cursor-compress-hooks` adds a global `preToolUse` hook (matcher `Shell`) that wraps terminal output through local compressors before it enters subscription Composer context.
- **Per-agent proxy savings on dashboard** — ledger entries tagged by agent (`x-agent` header, path prefix, or protocol inference when unambiguous).
- **Runtime fail-open shim** — proxy forwards directly to upstream when compression or inject fails.

### Changed
- **Hook skip when proxied** — proxied agents (claude, codex, goose, kimi, cline, opencode) skip hook inject; memory comes from the proxy path. Cursor and Copilot always inject via hooks (Cursor keeps memory even when its BYOK traffic is proxied).

## [0.10.1] - 2026-08-01

### Docs
- Expanded PyPI package description and keywords (claude-code, cursor, kimi, goose, ai-agents, ai-memory) so installs match the multi-agent + dual-path product.

## [0.10.0] - 2026-08-01

### Added
- **Dual-path context layer** — opt-in local proxy (`memor install-proxy`) compresses latest-turn tool payloads, forwards to Anthropic/OpenAI, and writes a savings ledger. Hooks remain the reliable memory path; proxy inject is best-effort. (#39, #40)
- **Kimi CLI + Goose hooks** — `memor install-hook --agent kimi|goose` with agent-correct response formatting. (#39, #40)
- **Multi-agent ingest** — daemon + `memor backfill` scan Claude Code, Kimi (`wire.jsonl`), and Goose (`sessions.db`). Model providers are not ingest sources. (#39, #40)
- **Proxy savings on the dashboard** — System Status metric cards (proxy/hook/daemon/agents + token savings), content-type breakdown, and CCR-aware ledger. (#39, #40)
- **Offline proxy benchmark** — packaged fixtures + `memor eval-proxy` release gate. (#40)
- **Optional local GGUF distillation** — `memor-cli[llm]` + `MEMOR_LLM_DISTILL=1` (default off; extractive remains the default). (#39)

### Fixed
- Proxy review hardening: valid JSON crush trailer (`_memor_note`), DB-recorded embed dim, Accept stream header parsing, proxy port-in-use warning on service install, packaged eval fixtures. (#40)

### Changed
- Dashboard System Status uses the same metric-card grid as Memory Bank / Agent Breakdown (no more single status blob).

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
