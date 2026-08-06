# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.12.0] - 2026-08-06

### Added
- **Jcode support** — jcode is now a first-class agent, though uniquely split across two channels because every jcode hook except `pre_tool` is a detached observer whose stdout is discarded. Hooks therefore drive ingest (`turn_end`, `session_end`, via `memor install-hook --agent jcode`) and MCP serves recall (`memor_recall`, via `memor install-mcp --agent jcode`). A jcode session's working directory is read from its journal sidecar when the session JSON leaves it null, which is what keeps its memories from being filed under "unknown" and leaking across projects. The daemon also scans `~/.jcode/sessions` as a safety net for sessions the hook missed.
- **`memor_recall` MCP tool** — memory search for any agent whose hooks cannot inject context. A miss names the projects that do hold memories rather than dead-ending, because an MCP server's working directory is whatever launched the agent and the default project is routinely wrong.

### Fixed
- **Recall was rejecting genuine matches by hundredths of a point.** The dense channel gated candidates at raw cosine 0.0, on the reasoning that relevant content scores above zero and noise below. These static embeddings have no such margin — a *good* match scores about 0.05, not 0.6 — so the floor sat inside the noise band. It is now -0.05. Replayed over every one of the 3,390 distinct real queries in a live store: queries returning nothing fall from 247 to 202 (7.3% → 6.0%), memories surfaced rise from 7,223 to 7,469 (+3.4%), and eight subjects the store has no history of still return nothing. The effect is largest on short, vague queries, which sit closest to the floor.
- **A git worktree was a separate project.** Project resolution stopped at the first `.git` and used that directory's name, but in a linked worktree `.git` is a file pointing back at the repository. Work done in `repo-feature-x` never surfaced while working in `repo`, and sibling worktrees could not see each other, so one repository splintered into as many memory buckets as it had checkouts. Worktrees now resolve to their repository.
- **Re-ingesting a session corrupted the vector index.** `add_artifacts` used `INSERT OR REPLACE`, which deletes and re-inserts and therefore assigns a *new* rowid; the vec index is keyed by rowid with no foreign key, so each rewrite orphaned the previous embedding. Three writes of four artifacts left twelve vec rows, and a later reused rowid collided with a stale vector, failed the primary key and lost the entire batch. Affected every agent.
- **`memor prune` retired 99.4% of distilled memories.** Deduplication keyed on (project, text) across kinds, but a `memory` is distilled *from* a `session_chunk` and the two carry identical text. The chunk is always older and the pass keeps the earliest copy, so the memory lost every time — 2,442 of 2,457 on a real store, silently downgrading recall from `ok` to `extractive_only`. Deduplication is now within a kind.
- **The daemon burned 90% CPU indefinitely.** Two Kimi sessions carrying attachments could not be parsed (`user_input` arrives as a list of content blocks, not a string), and because a file's state was recorded only after a successful parse they were retried on every 30s poll forever. Each retry re-ran the whole post-ingest pipeline, including a cross-project promotion scan that is O(n²) over every distilled memory and took 35s at this store's size. The parser handles both shapes, failures are recorded so a file is not retried until it changes, the similarity scan is vectorized (35.34s → 0.86s), and the whole-store sweeps run hourly rather than on every cycle that ingested anything.
- **The dashboard took 8–10 seconds to load.** Three endpoints each independently parsed the full transcript corpus — about a gigabyte — and the page requests all three at once, so a cold load did the same work three times concurrently. They now share one cache. Separately, the launchd units filed the dashboard and proxy under `ProcessType=Background`, which caps CPU and throttles disk I/O; both are now `Interactive`. Combined: 8–10s → 2.0s cold.
- **Recalls served over MCP were invisible.** Every per-agent dashboard view reads `recall_log`, and only the hook and proxy paths wrote to it, so an agent served purely over MCP did real work and appeared to do nothing.
- **The dashboard showed the raw `_global` scope name** in both recall tables and the project filter pill. It now renders as a "Global" badge everywhere, as the projects and quality tables already did.
- Quality scores are derived from a verdict ledger rather than incremented counters, which had credited one artifact with 2,180 uses against 40 recalls.

### Changed
- Retrieval diversifies its top-k with Maximal Marginal Relevance, so near-identical chunks no longer stack and spend the budget on one thing said five ways.
- Ingestion filters harness noise (interrupt markers, tool-rejection notices, subagent prompt headers) that a live store had accumulated 615 copies of.
- `memor prune` retires harness noise and cross-session duplicates. Rows are deactivated rather than deleted, so the pass is reversible and ids referenced by recall history stay valid.
- `memor recall-baseline` compares recall across a stamped boundary instead of requiring the store to be wiped.

### Compression work from earlier in the cycle (2026-08-02 to 08-04)

This was stamped 0.12.0 on 4 August but never published, so it reaches users here.

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
- `memor uninstall-proxy --agent cursor` and `memor service uninstall` clean up any leftover wire settings, service unit, and config keys. The wire was built and removed within this same unreleased cycle, so this only affects installs built from source — nothing on PyPI ever shipped it.

#### Also from that cycle
- **Cursor install automation** — `memor install-proxy --agent cursor` enables memory hooks, Shell compress hooks, and the BYOK proxy on `:8421`. Flag: `--yes`.
- **Agent desk dashboard** — Overview plus per-agent panes (Claude, Cursor, Codex, …) with cumulative savings equity curves, recall volume charts, and `/api/agent-desk`.
- Dashboard layout de-densified: status chips + portfolio KPIs on Overview; each agent desk shows focused KPIs and filtered recalls.
- Multi-IDE proxy installs (Cline, OpenCode, Cursor BYOK) and per-agent proxy attribution.

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
