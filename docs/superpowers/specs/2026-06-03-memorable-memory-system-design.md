# Memorable — Measured Memory for Coding Agents (V0 Design)

Date: 2026-06-03
Status: Design — pending user review

## Problem

Coding/research agents are amnesiac across sessions. Each new session re-reads the
same files, re-derives the same architecture, re-makes settled decisions, and
re-explains context the agent "knew" yesterday. Cost: wasted tokens, wasted
latency, and inconsistency (the agent contradicts decisions it helped make).

## Target

A performant memory that **stores all agent-related artifacts** (coding sessions,
research reports, notes, fetched pages) and **retrieves the right context fast,
scoped to a project/history the user chooses**, delivered through a **Claude Code
skill/plugin**. Primary win: the user pulls exactly the past context they need
into a fresh session instead of re-paying tokens to rebuild it.

## Thesis (what makes this worth building)

The retrieval infrastructure is a commodity. The under-served, defensible parts are:

1. **The write path** — knowing *what* to promote to long-term memory and *when*.
2. **Staleness/contradiction** — superseding reversed decisions, not accumulating them.
3. **Measured value** — *proving* recall surfaces the right thing, instead of asserting it.

The eval harness is the spine. Nothing ships without a delta against a baseline.

## Two consumers, one engine (sequencing)

The retrieval core is shared; the consumers differ only in *who calls it* and
*how we measure good*:

- **(A) Explicit recall — V0 PRIMARY.** User/agent calls with a query + scope;
  top-k relevant artifacts returned, fast, compact. Delivered as a Claude Code
  skill. Eval = search relevance + latency. Human in the loop ⇒ bad results are
  visible and cheap.
- **(B) Autonomous injection — fast-follow.** The *same* `query()` called
  automatically on a trigger, results compressed into the agent's context.
  Strictly = (A) + a trigger policy. Deferred because auto-injecting *bad/stale*
  memory silently degrades the agent — we must trust retrieval quality (provable
  only via A) before turning it on.

## Non-goals for V0

Neo4j, Redis, FastAPI, Next.js UI, Postgres, Docker, multi-tenancy, the
autonomous-injection trigger. Each is in the earn-the-complexity backlog with an
explicit measurement trigger.

## Architecture (V0: single process, CLI + skill)

```
ingest (any artifact → normalized store)
   → distill (session → typed memories, dedup + supersede edges)
      → query(text, scope) → scope filter → similarity → 1-hop edge expand
                            → rank (sim ⊕ recency ⊕ edge) → compact + trace
         → eval (runs suite, prints Δ vs baselines)
```

### Components & interfaces

- `MemoryStore` (interface). V0 impl: **SQLite + sqlite-vec**. Swappable to
  Postgres+pgvector later, no caller changes.
- `Embedder` (interface). Default local `sentence-transformers`
  (`bge-small-en-v1.5`); API impl available.
- `LLM` (interface). OpenAI-compatible + Anthropic impls. Used by the distiller
  and the optional eval judge. Local default for offline eval.
- `ingest/` — **universal artifact ingest**:
  - `claude_code.py` — parse `~/.claude/projects/**/*.jsonl` → sessions + chunks.
  - `documents.py` — markdown/notes/research reports + fetched pages → artifacts.
  - All normalize to one model (below).
- `retrieve/retriever.py` — scope filter → vector search → optional 1-hop edge
  expansion → blended rank → compaction. Emits a score-breakdown **trace** (the
  inspector data; JSON now, UI later).
- `distill/distiller.py` — LLM turns a finished session into typed memories
  (decision | lesson | snippet | bugfix) with dedup (embedding similarity) and
  **supersede** (write a `supersedes` edge, mark older inactive) on contradiction.
- `skill/` — a Claude Code skill wrapping the **same** `query()` the eval calls.
- `eval/` — dataset builder + metrics + runner.

### Data model (SQLite) — universal artifact store + edges

- `artifacts(id, kind, project, source, text, token_count, created_at, meta_json)`
  — `kind` ∈ {session_chunk, research, note, page, memory}; one table for everything retrievable.
- `sessions(id, source, project, started_at, raw_path, meta_json)` — provenance for session_chunk artifacts.
- `edges(src_id, dst_id, type)` — `type` ∈ {derived_from, supersedes, fixes, part_of}.
  Relationships that similarity can't infer from text. Multi-hop = recursive CTE.
- `embeddings` — sqlite-vec virtual table keyed by `artifact_id`.
- `eval_runs(id, created_at, config_json, metrics_json)`.

Relationships are first-class from day one as cheap edges + CTE traversal. A graph
*database* is deferred until an eval shows multi-hop helps AND a profiler shows
the CTE is the bottleneck.

## Eval harness (the spine)

**Headline metric:** search relevance of explicit recall — does scoped `query()`
return what the session actually needed, fast and compact?

- **Internal baselines (all three):**
  1. `no-memory` — nothing retrieved.
  2. `last-N` — last N turns of the same project, no retrieval.
  3. **`naive-RAG`** — vector search over *raw* chunks, no distill/dedup/supersede/edges.
     This is the baseline that tests the thesis.
- **External comparative baselines (adapter per system; behind a flag, run when available):**
  4. `claude-mem` — the Claude Code memory plugin, on the same corpus/queries.
  5. `graphiti` — Zep's temporal knowledge graph, on the same corpus/queries.
  Purpose: turn "are we better than existing tools?" from an argument into a number,
  and — if one of them wins on our eval — adopt it. The harness scoring existing
  memory systems on real coding sessions is itself a novel, defensible artifact.
- **Ablations:** `similarity-only` vs `similarity + 1-hop edge expansion`
  (does the edge layer earn its place?).
- **Dataset (counterfactual, auto-labeled):** for a held-out session N, extract
  what it *actually needed early* (files opened, identifiers/decisions referenced
  in its first turns); ask whether memory built from sessions 1…N−1 surfaces those.
  No biased hindsight labeling. Target ≥ ~50 cases per evaluated project to avoid
  an underpowered result.
- **Contradiction/supersede eval (the moat):** inject decision X, reverse to ¬X in
  a later session, query → memory must return ¬X and suppress X.
- **Metrics:** recall@k, nDCG@k (vs counterfactual need); retrieve() latency p50/p95;
  returned-context token count (compactness); **amortized token accounting**
  (distill write-cost ÷ future retrievals that reuse it — savings counted net of writes).
  Optional: task-quality LLM judge for the (B) injection follow-on.
- **Output:** metric + Δ-vs-each-baseline table; persisted to `eval_runs`.

## Earn-the-complexity backlog (measurement that justifies each)

- **Distillation/write-path improvements** — highest value; iterate first.
- **Multi-hop edge traversal** — only if 1-hop ablation shows edges help and deeper helps more.
- **Autonomous injection trigger** — only once (A) recall quality is proven on the corpus.
- **Hybrid search (BM25+vector)** — only if vector-only recall@k is the bottleneck.
- **Redis hot cache** — only if measured retrieve() p95 is unacceptable AND profiling blames the vector query.
- **Postgres+pgvector** — only when SQLite is a measured scale/concurrency limit.
- **Neo4j** — only if multi-hop helps in eval AND the CTE is the profiled bottleneck.
- **FastAPI + Next.js inspector UI** — once the CLI trace proves what's worth visualizing.

## Success criteria & kill criteria for V0

- **Success (outcome, not deliverable):** on a real corpus (e.g. `stablex`, 301
  sessions), scoped distilled recall beats `naive-RAG` on recall@k/nDCG by a margin
  that justifies the distill+edge complexity, at p50 latency low enough to feel
  instant in a skill call, returning a compact context block.
- **Kill criterion:** if distilled memory does **not** beat `naive-RAG`, abandon
  distillation and ship the simple retriever. If 1-hop edges don't move recall,
  drop the edge layer. We follow the numbers, not the architecture diagram.

## Project structure

```
memorable/
  pyproject.toml
  memorable/
    config.py
    interfaces.py            # Embedder, LLM, MemoryStore protocols
    store/sqlite_store.py
    embed/{local.py,api.py}
    llm/{base.py,openai_compat.py,anthropic.py}
    ingest/{claude_code.py,documents.py}
    retrieve/retriever.py
    distill/distiller.py
    eval/{dataset.py,metrics.py,runner.py}
    cli.py                   # ingest | distill | query | eval
  skill/                     # Claude Code recall skill (wraps query())
  evals/cases/
  tests/
```
