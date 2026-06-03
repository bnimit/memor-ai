```
                                                _
 _ __ ___   ___ _ __ ___   ___  _ __       __ _(_)
| '_ ` _ \ / _ \ '_ ` _ \ / _ \| '__|____ / _` | |
| | | | | |  __/ | | | | | (_) | | |_____| (_| | |
|_| |_| |_|\___|_| |_| |_|\___/|_|        \__,_|_|

  Measured memory for coding agents.
```

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-45%20passing-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)]()
[![Node](https://img.shields.io/badge/node-18%2B-green.svg)]()

**Store, distill, and retrieve past session context instead of re-sending full history to the LLM.**

Proven on real data: **99.7% token savings**, **11ms retrieval**, with an eval harness that measures every claim.

```
 ┌──────────────────────────────────────────────────────────────────┐
 │  Before memor              After memor                          │
 │                                                                  │
 │  487,755 tokens  ────►     72 - 1,384 tokens                   │
 │  (full history)            (scoped retrieval)                   │
 │                                                                  │
 │  Every new session         Only what's relevant,                │
 │  re-sends everything       distilled + ranked                   │
 └──────────────────────────────────────────────────────────────────┘
```

---

## Why

If you've spent any time with coding agents, you know the feeling. You open a new session, and the agent has no idea what happened yesterday. It doesn't remember that you decided to use argon2 for password hashing. It doesn't know you already fixed that auth bug last Tuesday. It doesn't remember the architecture you spent three sessions designing together.

So you explain it all again. Or you paste in old conversations. Or you just hope it figures it out.

That costs real money. A typical project accumulates hundreds of thousands of tokens in session history. Resending all of that every time you start a new chat isn't just slow — it burns through your context window and your API budget.

Memor is a quiet background process that watches your coding sessions, pulls out the important bits (decisions, patterns, bugfixes, architecture choices), and stores them in a local database. When you start a new session, your agent can ask memor "what do I need to know?" and get back just the relevant context — a few hundred tokens instead of half a million.

---

## The Honest Truth: It Gets Better Over Time

We want to be upfront about something. Memor isn't magic on day one.

```
  Your first week                         A month later

  "I just installed this thing.           "How did we fix the auth
   There's nothing in the database."       refresh bug last week?"

  That's normal.                           memor returns the exact decision
  Memor learns from YOUR sessions.         in 11ms, saving you from re-reading
  No sessions yet = nothing to recall.     487K tokens of old transcripts.
```

Here's what the journey actually looks like:

**Day 1 — Empty database.** You install memor and start the daemon. It begins watching your sessions, but there's nothing to retrieve yet. That's okay. Every session you run from now on is being quietly ingested and stored.

**End of week 1 — A few sessions in.** You've had maybe 5-10 coding sessions. Memor has extracted key chunks from each one. The first time you ask "how does auth work in this project?" and get a useful answer pulled from a conversation three days ago — that's when it clicks.

**Week 2+ — Compounding returns.** The more you use your coding agent, the more memor knows. Decisions accumulate. Patterns emerge. Bugfixes get catalogued. What used to require re-reading old sessions or re-explaining your codebase now comes back in milliseconds.

```
  Token savings over time

  100% ┤                                      ━━━━━━━━
       │                                 ╱
       │                            ╱
       │                       ╱
       │                  ╱
       │             ╱
       │        ╱
    0% ┤━━━━━━━
       └───────────────────────────────────────────────
         Day 1        Week 1         Week 2+
         (learning)    (useful)       (paying off)
```

### Jumpstarting your memory (skip the cold start)

If you don't want to wait, you have options:

**Already been using Claude Code?** You probably have session transcripts sitting in `~/.claude/projects/`. Ingest them all at once:

```bash
memor ingest-project ~/.claude/projects/-Users-you-your-project --project myproject
```

That could give you weeks or months of history immediately — no waiting required.

**Have project docs?** Feed them in. Your README, architecture docs, design decisions — anything that captures how your project works:

```bash
memor ingest-doc docs/architecture.md --project myproject
memor ingest-doc docs/decisions.md --project myproject
```

**Working on a team?** The database is a single file (`~/.memor/memor.db`). A teammate who's been running memor for a month can share their DB, and you inherit all that context instantly.

The point is: memor rewards you for the work you're already doing. Every conversation with your coding agent becomes a deposit into a memory bank that makes the next conversation cheaper and smarter.

---

## Quick Start

### Step 1: Install

```bash
npm install -g memor-ai
# or: bun install -g memor-ai
# or: npx memor-ai --help

# Requires Python 3.11+ (auto-detected and configured during install)
```

### Step 2: Start the daemon

```bash
memor daemon
```

That's it. The daemon runs in the background, watches `~/.claude/projects/` for new session transcripts, and automatically ingests and distills them. Every time you finish a Claude Code session, memor picks it up.

> **Tip:** If you've already been using Claude Code, you probably have a backlog of sessions. Bulk-ingest them so memor has something to work with right away:
> ```bash
> memor ingest-project ~/.claude/projects/-Users-you-your-project --project myproject
> ```

### Step 3: Connect memor to your agent

This is the important part that makes the whole thing work. **The daemon builds the memory, but your agent needs a way to read it.**

Memor ships with a skill for Claude Code. To set it up, add the skill path to your project's `.claude/settings.json`:

```json
{
  "skills": ["path/to/memor-ai/skill/SKILL.md"]
}
```

Or, if you installed globally, find the path with:
```bash
npm root -g
# → /usr/local/lib/node_modules
# So the skill is at: /usr/local/lib/node_modules/memor-ai/skill/SKILL.md
```

Once registered, Claude Code can invoke the recall skill whenever it needs past context. You can ask it directly ("what did we decide about auth?") or it can pull context before starting related work.

### How it all fits together

```
 ┌────────────────────────────────────────────────────────────────────┐
 │                                                                    │
 │   You code with Claude Code as usual. Nothing changes.            │
 │                                                                    │
 │   ┌──────────────────────────┐                                    │
 │   │  Session ends            │                                    │
 │   │  (.jsonl written)        │                                    │
 │   └────────────┬─────────────┘                                    │
 │                │                                                   │
 │                ▼                                                   │
 │   ┌──────────────────────────┐                                    │
 │   │  memor daemon            │  (runs in background)              │
 │   │  • picks up transcript   │                                    │
 │   │  • filters noise         │                                    │
 │   │  • embeds + stores       │                                    │
 │   │  • distills into         │                                    │
 │   │    compact memories      │                                    │
 │   └────────────┬─────────────┘                                    │
 │                │                                                   │
 │                ▼                                                   │
 │   ┌──────────────────────────┐                                    │
 │   │  ~/.memor/memor.db       │  (your memory bank)                │
 │   │  decisions, patterns,    │                                    │
 │   │  bugfixes, architecture  │                                    │
 │   └────────────┬─────────────┘                                    │
 │                │                                                   │
 │                │  Next session starts...                           │
 │                ▼                                                   │
 │   ┌──────────────────────────┐                                    │
 │   │  Claude Code + skill     │                                    │
 │   │                          │                                    │
 │   │  "What do I need to      │                                    │
 │   │   know about auth?"      │                                    │
 │   │                          │                                    │
 │   │  skill/recall.py runs    │                                    │
 │   │  → returns 384 tokens    │  ← instead of 487,755 tokens      │
 │   │    of relevant context   │     of full history                │
 │   └──────────────────────────┘                                    │
 │                                                                    │
 │   The agent now has the context it needs without you               │
 │   re-explaining anything or re-sending old conversations.         │
 │                                                                    │
 └────────────────────────────────────────────────────────────────────┘
```

### Without the skill (manual mode)

If you don't want to set up the skill, you can still use memor directly from your terminal:

```bash
# Ask memor what it knows
memor query "how does auth work" --project myproject

# Distill sessions manually
memor distill --project myproject

# Browse everything in the visual inspector
memor inspector
```

You can copy the output and paste it into your agent's context. It's more manual, but it still saves you from digging through old sessions yourself.

---

## How It Works

```
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                         THE MEMOR PIPELINE                          │
 │                                                                         │
 │   Session ends                                                          │
 │       │                                                                 │
 │       ▼                                                                 │
 │   ┌──────────┐     ┌──────────────┐     ┌───────────────────┐          │
 │   │  Ingest  │────►│ Noise Filter │────►│  SQLite + Vectors │          │
 │   │ (daemon) │     │ (regex/TF)   │     │   (sqlite-vec)    │          │
 │   └──────────┘     └──────────────┘     └────────┬──────────┘          │
 │                                                   │                     │
 │                                                   ▼                     │
 │                           ┌──────────────────────────────────┐          │
 │                           │  Two-Step Distillation            │          │
 │                           │                                    │          │
 │                           │  Step 1: Extractive (free, local) │          │
 │                           │    TF-IDF + clustering + heuristics│          │
 │                           │    315K tokens ──► 56K tokens      │          │
 │                           │                                    │          │
 │                           │  Step 2: Abstractive (LLM)        │          │
 │                           │    56K tokens ──► 1.6K tokens      │          │
 │                           │    typed memories with dedup       │          │
 │                           └──────────────┬───────────────────┘          │
 │                                           │                             │
 │   New session starts                      │                             │
 │       │                                   ▼                             │
 │       ▼                          ┌─────────────────┐                    │
 │   ┌────────┐    scope + query    │  Memory Store   │                    │
 │   │ Agent  │───────────────────►│  (artifacts +   │                    │
 │   │ /Skill │◄───────────────────│   edges + vecs) │                    │
 │   └────────┘   100-1000 tokens   └─────────────────┘                    │
 │                  in <15ms                                               │
 └─────────────────────────────────────────────────────────────────────────┘
```

### Storage

Single SQLite file with [sqlite-vec](https://github.com/asg017/sqlite-vec) for vector search. Zero infrastructure. No Redis, no Postgres, no Docker.

```
 ┌─────────────────────────────────────────────────────────────┐
 │  ~/.memor/memor.db                                           │
 │                                                               │
 │  artifacts ──── id, kind, project, text, token_count, meta   │
 │      │                                                        │
 │      ├── session_chunk   (raw ingested turns)                │
 │      ├── memory          (distilled decisions/lessons)        │
 │      ├── note            (markdown docs, research)            │
 │      └── research        (imported documents)                 │
 │                                                               │
 │  vec_artifacts ── HNSW index, cosine distance, 384-dim       │
 │                                                               │
 │  edges ── src → dst with typed relationships                 │
 │      ├── derived_from    (memory ← source chunks)            │
 │      ├── supersedes      (new memory → old contradicted one) │
 │      ├── fixes           (bugfix → original issue)           │
 │      └── part_of         (chunk → session grouping)          │
 └─────────────────────────────────────────────────────────────┘
```

### Retrieval

```
 Query: "how does auth work?"
 Scope: project=myapp

      ┌───────────────────────────────────────────────────┐
      │                  Retriever                         │
      │                                                     │
      │  1. Embed query  ──►  384-dim vector               │
      │  2. Vec search   ──►  top-k by cosine sim          │
      │  3. Scope filter ──►  project + time + kind        │
      │  4. Recency blend ──► 0.8 * sim + 0.2 * recency   │
      │  5. Edge expand  ──►  1-hop neighbors (recursive)  │
      │  6. Re-rank      ──►  final scored list            │
      │                                                     │
      │  Output: RetrievalTrace                            │
      │    hits:       [{score, artifact, components}]      │
      │    latency_ms: 11.2                                │
      │    tokens:     384                                  │
      └───────────────────────────────────────────────────┘
```

### Distillation

The two-step pipeline cuts LLM costs by **82%**:

```
 ┌───────────────────────────────────────────────────────────────┐
 │                                                                │
 │  Raw session: 315,860 tokens                                  │
 │       │                                                        │
 │       ▼                                                        │
 │  ┌──────────────────────────────────┐                          │
 │  │ Step 1: Extractive  (FREE)       │                          │
 │  │  - TF-IDF rare-term scoring      │ ──► 56,335 tokens       │
 │  │  - Embedding k-means clustering  │     (82% reduction)     │
 │  │  - Heuristic signal detection    │                          │
 │  │  - Temporal order preserved      │                          │
 │  └──────────────────────────────────┘                          │
 │       │                                                        │
 │       ▼                                                        │
 │  ┌──────────────────────────────────┐                          │
 │  │ Step 2: Abstractive  (LLM)      │                          │
 │  │  - Typed memory extraction       │ ──► ~1,600 tokens       │
 │  │    (decision/lesson/bugfix/...)  │     (99.5% total)       │
 │  │  - Dedup (0.92 cosine thresh)   │                          │
 │  │  - Contradiction supersede       │                          │
 │  └──────────────────────────────────┘                          │
 │                                                                │
 │  No API key? Step 1 alone stores key chunks as memories.      │
 │  Quality: good. Cost: $0.                                     │
 │                                                                │
 └───────────────────────────────────────────────────────────────┘
```

---

## Commands

| Command | Description |
|---|---|
| `memor daemon` | Auto-ingest daemon. Watches `~/.claude/projects/` for new transcripts, auto-distills |
| `memor ingest-project <dir>` | Bulk ingest a project's transcripts |
| `memor ingest-cc <file>` | Ingest a single Claude Code transcript |
| `memor ingest-doc <file>` | Ingest a markdown or research document |
| `memor distill --project <name>` | Distill sessions into compact memories |
| `memor query <text>` | Query for relevant context |
| `memor eval <cases.json>` | Run eval suite against 4 baselines |
| `memor build-cases --project <name>` | Auto-generate eval cases from corpus |
| `memor inspector` | Launch Streamlit visual inspector |
| `memor setup` | Re-run Python environment setup |

All commands support `--db <path>` to target a specific database and `--project <name>` for scoping.

---

## Eval Harness

Nothing ships without a measured delta. The eval harness runs every retrieval strategy against 4 baselines on your own data:

```
 ┌─────────────────────────────────────────────────────────────┐
 │  Baselines                                                   │
 │                                                               │
 │  1. no-memory     Zero retrieval. The control.               │
 │  2. last-N        Most recent k chunks. Recency-only.        │
 │  3. naive-RAG     Cosine similarity. No edges, no recency.   │
 │  4. memory        Full pipeline: sim + recency + edges.      │
 │                                                               │
 │  Metrics per strategy:                                       │
 │    recall@k, nDCG@k, tokens_sent, token_savings,            │
 │    latency_ms (p50 + p95)                                    │
 │                                                               │
 │  Additional evals:                                           │
 │    - Edge expansion ablation (sim-only vs sim+edges)         │
 │    - Contradiction detection (stale superseded, current kept)│
 │    - Counterfactual auto-labeling from real transcripts      │
 │                                                               │
 │  External baseline adapters:                                 │
 │    - Graphiti (adapter stub, gated by availability)          │
 │    - claude-mem (adapter stub, gated by availability)        │
 └─────────────────────────────────────────────────────────────┘
```

```bash
# Generate eval cases from your project's data
memor build-cases --project myproject --db ~/.memor/memor.db

# Run the suite
memor eval cases.json --db ~/.memor/memor.db
```

---

## Inspector UI

A Streamlit-based visual inspector with 4 tabs:

```
 ┌──────────────────────────────────────────────────────────────┐
 │  Memor Inspector                                         │
 │  ┌─────────┬─────────┬─────────┬─────────┐                 │
 │  │  Query  │ Browse  │  Eval   │  Edges  │                 │
 │  └────┬────┴────┬────┴────┬────┴────┬────┘                 │
 │       │         │         │         │                        │
 │  Retrieval   Artifact   Run eval   Explore                  │
 │  inspector   browser    from UI +  relationships            │
 │  with score  filter by  view past  outgoing +               │
 │  breakdowns  project/   runs with  incoming                 │
 │  per hit     kind/text  metrics    edges                    │
 └──────────────────────────────────────────────────────────────┘
```

```bash
memor inspector
# Opens http://localhost:8501
```

---

## Claude Code Skill — How It Works Under the Hood

When the agent invokes the recall skill, here's what happens in about 11 milliseconds:

1. Your query ("how does auth work?") gets embedded into a 384-dimension vector
2. That vector is matched against every stored artifact using cosine similarity
3. Results are filtered by project scope, so you only get context from *this* project
4. Recent artifacts get a slight boost (configurable — default 20% recency weight)
5. Related artifacts are pulled in via edge expansion (if a memory was derived from a session chunk, that chunk can surface too)
6. The top results come back as a formatted context block with score breakdowns

The agent reads this context block and uses it to inform its work. It might cite a specific past decision, avoid re-introducing a bug that was already fixed, or pick up where a previous session left off.

```bash
# What the agent runs behind the scenes:
python skill/recall.py --query "how does auth work" --project myproject --k 8

# What it gets back:
## Recalled Context (project: myproject)

### 1. [memory] Use argon2 for password hashing — memory-hard, resistant to GPU attacks
Source: session a1b2c3d4, 2026-05-28 | score: 0.847

### 2. [session_chunk] The auth refresh loop bug is caused by re-issuing tokens on 401...
Source: session e5f6g7h8, 2026-05-25 | score: 0.793

---
Trace: 8 hits, 11ms, 384 tokens recalled
```

384 tokens. Not 487,755. That's the difference.

---

## Architecture

```
 memor/
 ├── types.py              Core dataclasses: Artifact, Scope, Hit, RetrievalTrace
 ├── interfaces.py         Protocols: Embedder, LLM, MemoryStore
 ├── cli.py                Typer CLI entry point
 ├── daemon.py             Auto-ingest + auto-distill background watcher
 │
 ├── store/
 │   └── sqlite_store.py   SQLite + sqlite-vec (artifacts, edges, vector search)
 │
 ├── embed/
 │   ├── local.py          sentence-transformers (bge-small-en-v1.5, 384-dim)
 │   ├── api.py            OpenAI-compatible embedding API
 │   └── fake.py           Deterministic SHA-256 embedder (tests)
 │
 ├── llm/
 │   ├── base.py           Distillation prompt template
 │   ├── anthropic.py      Anthropic Claude wrapper
 │   └── openai_compat.py  OpenAI-compatible wrapper
 │
 ├── ingest/
 │   ├── claude_code.py    JSONL transcript parser + noise filter
 │   └── documents.py      Markdown/research doc parser
 │
 ├── retrieve/
 │   └── retriever.py      Scope + vector search + edge expansion + recency blend
 │
 ├── distill/
 │   ├── extractive.py     TF-IDF + clustering + heuristics (LLM-free)
 │   └── distiller.py      Two-step: extractive pre-filter + LLM abstractive
 │
 └── eval/
     ├── metrics.py        recall@k, nDCG@k
     ├── dataset.py        EvalCase, counterfactual case builder
     ├── runner.py          4-baseline runner + ablation + contradiction eval
     └── baselines/        External baseline adapters (Graphiti, claude-mem)

 inspector.py              Streamlit UI (4 tabs)
 skill/recall.py           Claude Code recall skill
 bin/memor.mjs             Node.js CLI wrapper
 scripts/postinstall.mjs   Auto-venv + dependency setup

 tests/                    45 tests across 17 files
```

---

## Landscape Comparison

```
 ┌──────────────────┬─────────────┬──────────────┬────────────────┬──────────────┐
 │                  │  memor-ai   │  Graphiti    │  claude-mem    │  mem0        │
 │                  │             │  (Zep)       │                │              │
 ├──────────────────┼─────────────┼──────────────┼────────────────┼──────────────┤
 │                  │             │              │                │              │
 │ Target use case  │ Coding      │ General      │ Claude Code    │ General      │
 │                  │ agents      │ knowledge    │ sessions       │ chatbot      │
 │                  │             │ graphs       │                │ memory       │
 │                  │             │              │                │              │
 ├──────────────────┼─────────────┼──────────────┼────────────────┼──────────────┤
 │                  │             │              │                │              │
 │ Infrastructure   │ SQLite      │ Neo4j +      │ SQLite         │ Cloud API    │
 │                  │ (single     │ hosted       │                │ or           │
 │                  │  file)      │ service      │                │ Qdrant +     │
 │                  │             │              │                │ Postgres     │
 │                  │             │              │                │              │
 ├──────────────────┼─────────────┼──────────────┼────────────────┼──────────────┤
 │                  │             │              │                │              │
 │ Eval harness     │ Built-in    │ None         │ None           │ None         │
 │                  │ 4 baselines │              │                │              │
 │                  │ + ablation  │              │                │              │
 │                  │             │              │                │              │
 ├──────────────────┼─────────────┼──────────────┼────────────────┼──────────────┤
 │                  │             │              │                │              │
 │ Distillation     │ Two-step    │ LLM-only     │ None           │ LLM-only    │
 │                  │ (82% cost   │ (full input  │ (raw storage)  │              │
 │                  │  reduction) │  to LLM)     │                │              │
 │                  │             │              │                │              │
 ├──────────────────┼─────────────┼──────────────┼────────────────┼──────────────┤
 │                  │             │              │                │              │
 │ Graph model      │ Lightweight │ Full Neo4j   │ None           │ None         │
 │                  │ edges table │ property     │                │              │
 │                  │ + recursive │ graph        │                │              │
 │                  │ CTE (1-hop) │              │                │              │
 │                  │             │              │                │              │
 ├──────────────────┼─────────────┼──────────────┼────────────────┼──────────────┤
 │                  │             │              │                │              │
 │ LLM-free mode    │ Yes         │ No           │ Yes            │ No           │
 │                  │ (extractive │              │ (raw only)     │              │
 │                  │  fallback)  │              │                │              │
 │                  │             │              │                │              │
 ├──────────────────┼─────────────┼──────────────┼────────────────┼──────────────┤
 │                  │             │              │                │              │
 │ Scoped retrieval │ Project +   │ Per-user     │ Per-project    │ Per-user     │
 │                  │ time + kind │              │                │              │
 │                  │             │              │                │              │
 ├──────────────────┼─────────────┼──────────────┼────────────────┼──────────────┤
 │                  │             │              │                │              │
 │ Setup            │ npm install │ Docker +     │ pip install    │ Cloud signup │
 │                  │ (auto-venv) │ Neo4j +      │                │ or Docker    │
 │                  │             │ config       │                │              │
 │                  │             │              │                │              │
 ├──────────────────┼─────────────┼──────────────┼────────────────┼──────────────┤
 │                  │             │              │                │              │
 │ Embeddings       │ Local       │ OpenAI API   │ Local          │ OpenAI API   │
 │                  │ (offline,   │ (paid,       │                │ (paid)       │
 │                  │  free)      │  online)     │                │              │
 │                  │             │              │                │              │
 └──────────────────┴─────────────┴──────────────┴────────────────┴──────────────┘
```

### Where memor-ai wins

```
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                                                                         │
 │  1. MEASURED, NOT CLAIMED                                               │
 │     Every feature has a measured delta. The eval harness is built in.  │
 │     Other tools say "better retrieval" — we show recall@k numbers.    │
 │                                                                         │
 │  2. ZERO INFRASTRUCTURE                                                │
 │     One SQLite file. No Docker, no Neo4j, no cloud account.           │
 │     npm install and you're done.                                       │
 │                                                                         │
 │  3. CODING-AGENT NATIVE                                                │
 │     Built for the specific patterns of agentic coding: session         │
 │     transcripts, decisions, bugfixes, architecture context.            │
 │     Not a general chatbot memory bolted onto dev tools.                │
 │                                                                         │
 │  4. COST-AWARE DISTILLATION                                            │
 │     Two-step pipeline means 82% less LLM input. Extractive-only       │
 │     mode means $0 when you don't have an API key.                     │
 │                                                                         │
 │  5. ACTUALLY OFFLINE                                                   │
 │     Local embeddings (sentence-transformers). Local storage (SQLite).  │
 │     Optional LLM for distillation. Everything else works offline.     │
 │                                                                         │
 └─────────────────────────────────────────────────────────────────────────┘
```

### When to use something else

- **You need a full knowledge graph** with complex multi-hop reasoning across entities: use **Graphiti**
- **You want a hosted service** with zero self-management: use **mem0 Cloud**
- **You only use Claude Code** and want minimal setup with no distillation: use **claude-mem**

---

## Configuration

### Environment Variables

| Variable | Purpose | Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | LLM distillation (Anthropic Claude) | For abstractive distillation |
| `OPENAI_API_KEY` | Alternative LLM via OpenAI-compatible API | If not using Anthropic |
| `OPENAI_BASE_URL` | Custom endpoint (e.g., local Ollama) | If using OpenAI-compat |

### Daemon Behavior

| With API key | Without API key |
|---|---|
| Auto-ingest + two-step distill | Auto-ingest + extractive-only distill |
| Sharp one-liner memories | Best-of-session chunk memories |
| ~$0.001/session | Free |

---

## Development

```bash
# Clone and install in dev mode
git clone https://github.com/nimitbhandari/memor-ai.git
cd memor-ai
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,local,anthropic]"

# Run tests (45 tests, all pass)
pytest

# Run the inspector locally
streamlit run inspector.py
```

### Testing philosophy

- Every module has corresponding tests (`test_*.py`)
- `FakeEmbedder` (deterministic, SHA-256 based) for reproducible tests
- No mocks for the database — tests hit real SQLite
- Eval harness validates retrieval quality, not just code correctness

---

## Roadmap

- [ ] Autonomous recall (agent auto-injects context without explicit skill call)
- [ ] Multi-project cross-retrieval (pull patterns from project A into project B)
- [ ] Incremental re-distillation (update memories as sessions grow)
- [ ] Plugin system for custom ingest formats (Cursor, Copilot, etc.)
- [ ] Web UI for non-CLI users

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to contribute.

## Security

See [SECURITY.md](SECURITY.md) for our security policy and how to report vulnerabilities.

## License

MIT. See [LICENSE](LICENSE) for the full text.
