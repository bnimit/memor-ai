# memor: competitive and technical landscape

Research date: 2026-08-22. All URLs read on 2026-08-22 unless noted.
No code was modified in producing this report.

---

## 0. Executive summary

Four findings, in order of how much they should change decisions:

1. **The compression half of memor is the weaker half, and the platform has
   already absorbed most of it.** Anthropic shipped server-side context editing
   (tool result clearing) on 2025-09-29 and reports 84% token reduction on a
   100-turn eval. Claude Code auto-compacts natively. memor's own measured
   blended saving is 0.8% on proxied traffic and 3.9% on a whole request. These
   are not competing on the same axis, but a buyer will not distinguish them.
2. **The memory half is the defensible half, and it is crowded.** Nine-plus
   funded or high-star competitors, most cloud-first. memor's differentiator is
   local-first plus measured-on-your-own-traffic honesty, not mechanism.
3. **Token cost is almost certainly not the binding constraint.** On Claude Max
   the marginal token is free until a rate limit bites. The binding constraints
   are context window exhaustion, compaction quality loss, and re-explaining the
   codebase. memor's memory feature attacks the third directly. Its compression
   feature attacks a constraint most target users do not have.
4. **The single largest technical risk is prompt-cache interaction.** Anthropic's
   own docs state that tool result clearing invalidates cached prefixes. Any
   third-party rewrite of mid-conversation content has the same problem, and a
   cache miss can cost more than the tokens saved.

Confidence: **high** on platform features and competitor facts (primary sources,
dated). **Medium** on the adoption/binding-constraint argument (inference from
pricing structure and public complaints, not from install telemetry).
**Low** on anything about private roadmaps, which I have not speculated about.

---

## 1. Direct competitors

### 1.1 Summary table

| Product | Core mechanism | Local-first? | Business model | Direct overlap with memor |
|---|---|---|---|---|
| **Headroom** | Compress tool outputs / logs / RAG chunks before the model sees them; library, proxy, MCP, `wrap` for Claude Code / Codex / Cursor | Yes, "local-first, reversible", Apache 2.0 | OSS + enterprise VPC/SLA | **Near-identical.** This is the closest competitor by far |
| **claude-mem** | Captures agent session activity, AI-compresses it, injects relevant context into future sessions; hooks into Claude Code, Codex, Cursor, others | Local install (npm/bun), optional sync hub | OSS | **High** on the memory half |
| **Letta (MemGPT)** | Agent-managed tiered memory; the agent edits its own memory blocks via tools ("LLM as OS") | Self-host possible; product is a cloud platform + CLI | $0 free / $20 Pro / $20+usage API / $20 seat Teams / Enterprise | Medium: framework not a layer |
| **Mem0** | Extract-consolidate-retrieve salient facts, optional graph memory | OSS core + hosted platform | Free / $19 / $249 / Enterprise, metered by add + retrieval requests | Medium |
| **OpenMemory** (Mem0) | MCP memory server for coding agents specifically: auto-captures coding prefs, serves per-project | MCP local client, Mem0 backend | Part of Mem0 | **High**: same user, same surface |
| **Zep / Graphiti** | Temporal context graph; facts carry validity windows, provenance to "episodes" | Graphiti OSS self-hostable; Zep is cloud/BYOC | Credit-based: $1,250/yr Flex (50k credits/mo), $3,750/yr Flex Plus, Enterprise | Low-medium: enterprise, not dev tool |
| **Cognee** | Turns captured context into graph memory; integrations for Claude Code, Codex, MCP | `pip install cognee` local, then Cognee Cloud | OSS + cloud | Medium-high |
| **Supermemory** | "Context infrastructure": memory graph, SuperRAG, search/traversal, priced per SM token | Self-host only on Scale/Enterprise | $0 / $19 / $100 / $399 per month usage-bundled | Medium |
| **Honcho** (Plastic Labs) | Custom reasoning model ("Neuromancer") continually reasons over messages; `context()` returns curated context under a token budget | Cloud API, plugins for Claude Code / Codex / OpenClaw | Per-query tiers $0.001 to $0.50 | Medium-high: explicitly claims token savings |

### 1.2 What each actually claims

- **Headroom** claims "60-95% fewer tokens (for JSON data), 15-20% fewer tokens
  (for coding agents)". Note the honesty of the split: their coding-agent number,
  15-20%, is the same order of magnitude as memor's 11.7% on tool output. Two
  independent teams measuring the same thing and landing in the same band is
  corroboration that the ceiling is real. Headroom's site claims 24.9k GitHub
  stars in one place and the repo page renders 67.2k; I could not reconcile
  these, treat the star count as **unverified**.
- **Mem0** claims 26% relative improvement over OpenAI's memory on LOCOMO,
  91% lower p95 latency, >90% token cost saving vs full-context (arXiv
  2504.19413, submitted 2025-04-28).
- **Zep** publicly disputes that: 75.14% vs Mem0's best, "outperforming Mem0 by
  10%", and argues LOCOMO is a flawed benchmark (16-26k token conversations,
  missing ground truth in category 5, incorrect speaker attribution). Critically,
  Zep points out **Mem0's own paper shows a plain full-context baseline beating
  Mem0** (~73% vs ~68%). That is the single most important number in the
  competitive literature and it cuts against every memory vendor including memor.
- **Honcho** claims 60-90% token savings and SOTA on LoCoMo 89.9%, LongMem S
  90.4%, BEAM. Self-reported, benchmarks published to their own eval site.
- **Letta** is now primarily a coding-agent product in its own right
  (`@letta-ai/letta-code`), not a memory library. The V1 memory server is
  archived. This is a competitor **moving up the stack into being the harness**,
  which is a different threat model.

### 1.3 The honest read

memor's mechanism is not novel. Compression-before-the-model is Headroom.
Session-capture-and-reinject is claude-mem. Fact extraction plus retrieval is
Mem0/OpenMemory. What memor has that the field mostly does not is a **falsifiable
measurement discipline**: publishing 0.8% blended and explaining why the
denominator makes it 0.8%, when competitors publish 60-95%. That is a real
differentiator with a narrow audience: it appeals to the engineer who has already
been burned by a vendor's benchmark, and it is invisible to everyone else.

---

## 2. Platform-absorption risk

This is the question that decides the product.

### 2.1 What has already shipped

| Capability | Status | Date | Source |
|---|---|---|---|
| **Context editing** (`clear_tool_uses_20250919`) - server-side clearing of old tool results | Public beta at announcement; docs now say "available on all supported Claude models", still behind `context-management-2025-06-27` beta header | Announced 2025-09-29 | anthropic.com/news/context-management; docs.anthropic.com/en/docs/build-with-claude/context-editing |
| **Thinking block clearing** (`clear_thinking_20251015`) | Same beta header | strategy string dated 2025-10-15 | context-editing docs |
| **Memory tool** (`memory_20250818`) | GA-ish: "available on all Claude 4 and later models", no beta header required. Client-side, SDK helpers in Python/TS/C#/Java, `BetaLocalFilesystemMemoryTool` ships ready-made | Announced 2025-09-29 | docs.anthropic.com/en/docs/agents-and-tools/tool-use/memory-tool |
| **Claude Code auto-compact** | Shipped, on by default, configurable auto-compact window | Referenced across current docs | code.claude.com/docs/en/costs, /model-config |
| **Client-side SDK compaction** | In TypeScript and Ruby SDKs via `tool_runner`; docs say "server-side compaction is generally preferred" | current | context-editing docs |
| **OpenAI compaction** | Documented core-concept guide in the OpenAI API docs | current | developers.openai.com/api/docs/guides/compaction |
| **Prompt caching** | Both providers, GA | current | docs on both platforms |
| **Cursor Memories** | Shipped as beta in Cursor 1.0 | 2025-06-04 | cursor.com/changelog/1-0 |

### 2.2 The numbers Anthropic published

From the 2025-09-29 announcement:

- memory tool + context editing: **+39%** over baseline on an internal agentic
  search eval.
- context editing alone: **+29%**.
- 100-turn web search eval: context editing let agents complete workflows that
  would otherwise fail from context exhaustion, **while reducing token
  consumption by 84%**.

Read that last figure carefully before panicking. 84% is measured on a 100-turn
web-search agent, where tool results are almost the entire request and where the
alternative is failure. It is a different denominator from memor's, exactly the
kind of denominator memor's own README warns about. But a prospective user will
compare "84%" to "11.7%" and stop reading.

### 2.3 What is left for a third party

Honestly, four things, and only four:

1. **The memory tool is deliberately unimplemented.** Anthropic ships the
   protocol and a reference local-filesystem handler; the docs say plainly
   "Memory lives entirely in your application" and "you control where and how the
   data is stored". They have specified the socket, not the appliance. What goes
   *into* memory, when, deduplicated how, retrieved with what ranking, is
   unowned. memor's LongMemEval numbers (95.0% any-hit, 86.7% all-gold) are
   evidence in exactly this gap. **This is the strongest remaining position.**
2. **Cross-harness portability.** Anthropic's features work with Claude. A
   developer using Claude Code, Codex, and Cursor in one week has three memory
   silos. Every serious competitor has noticed this: claude-mem, Cognee, Honcho,
   and Headroom all list multi-harness support prominently. This is a real,
   durable, and un-absorbable gap, because no model provider will make its memory
   work well inside a rival's harness.
3. **Local-first as a compliance and trust position.** Anthropic's memory tool is
   client-side by design, so "local" is not itself a differentiator against
   Anthropic. It is a differentiator against Mem0, Zep, Supermemory, and Honcho,
   which are cloud APIs. memor is competing on privacy against the *other
   startups*, not against the platform.
4. **Measurement itself.** `request-anatomy`, `hook-worth`, `eval-retention` are
   a product feature no platform will ship, because no platform wants a tool that
   prints how little its own optimisation saved.

### 2.4 What is not left

Generic conversation compaction. Clearing stale tool results. Summarising history
at 95% context. These are done, free, in the harness, on by default, and better
positioned than any proxy can be because they run server-side before the prompt
reaches the model and have the model's own token accounting.

### 2.5 The cache-invalidation trap

The context editing docs state directly:

> "Tool result clearing: Invalidates cached prompt prefixes when content is
> cleared... You'll incur cache write costs each time content is cleared."

And they added `clear_at_least` specifically so that clearing is worth the
invalidation. This is Anthropic conceding that naive context reduction can be net
negative. **Any third-party layer that rewrites content inside the cached prefix
inherits this problem and cannot see the provider's cache state to reason about
it.** memor's README already shows awareness (it declines to touch conversation
text because "rewriting it re-forms the provider's cached prefix"), which is
correct and to its credit. But it constrains memor to the 33% tool-result slice
permanently, and that constraint is arithmetic, not engineering.

---

## 3. Where the research is going

### 3.1 Does filling a large window degrade quality? Yes, unambiguously.

- **Chroma, "Context Rot" (2025-07-14, research.trychroma.com/context-rot):** 18
  models including GPT-4.1, Claude 4, Gemini 2.5, Qwen3. "Models do not use their
  context uniformly; performance grows increasingly unreliable as input length
  grows." They held *task complexity constant* and varied only length, which is
  the methodological point that makes the result credible. Even the trivial
  repeated-words task degrades.
- **NoLiMa (arXiv 2502.05167, ICML 2025):** removing lexical overlap between
  needle and question collapses long-context performance. At 32K, **11 of 13
  models drop below 50% of their short-context baseline**. GPT-4o falls from
  99.3% to 69.7%. Cause attributed to attention struggling without literal
  matches.
- **RULER (arXiv 2404.06654, COLM 2024):** models claiming 32K+ contexts, "only
  half of them can maintain satisfactory performance at the length of 32K",
  despite near-perfect vanilla NIAH.
- **Anthropic's own engineering post (2025-09-29):** cites context rot by name,
  frames context as "a finite resource with diminishing marginal returns" and
  attention as a budget depleted by every token, grounded in the n² pairwise
  attention argument. They describe it as "a performance gradient rather than a
  hard cliff".

Needle-in-a-haystack is thoroughly discredited as a long-context measure. All
four sources say so independently.

### 3.2 What this implies for memor

Here is the uncomfortable inference, stated plainly.

**The research strongly supports the thesis "less context is better context". It
does not support the thesis "saving 7% of context matters".**

The degradation curves in Chroma and NoLiMa are gradual over orders of magnitude.
NoLiMa's collapse happens between 1K and 32K, a 32x change. Nothing in this
literature suggests a measurable quality difference between a 100k-token context
and a 93k-token context. If memor's justification for compression is "quality,
not cost", the research does not currently supply the evidence, and memor would
need to generate it: an eval showing task success rate at N tokens vs 0.93N
tokens on real coding sessions. That is a hard eval to win, because the effect
size is probably below noise.

Where the research *does* support memor is elsewhere:
- Anthropic's own framing of "just-in-time" retrieval, keeping lightweight
  identifiers and loading on demand, is exactly the memory-and-recall pattern.
- The Zep observation that a full-context baseline beat Mem0 on LOCOMO is the
  sharpest warning available: a memory layer that retrieves imperfectly is worse
  than no memory layer when the content fits. memor's 95.0% any-hit on
  LongMemEval is the right number to defend, because LongMemEval's ~115k-token
  conversations are past the point where full-context is free.

### 3.3 KV-cache management

Prompt caching is the deployed form of KV-cache reuse and it changes the
economics in a way that is hostile to mid-prefix rewriting. Anthropic's guidance
to keep thinking blocks in order to preserve cache hits, and to clear "at least"
enough tokens to justify invalidation, both point the same way: **stability of
the prefix is now a first-class cost term**. A compression layer is in tension
with caching by construction. This is a structural headwind, not a bug to fix.

---

## 4. What actually gets adopted

### 4.1 Is token cost the binding constraint? Almost certainly not, for the target user.

- Claude Code docs state plainly: "Claude Max and Pro subscribers have usage
  included in their subscription, so the session cost figure isn't relevant for
  billing purposes." A Max subscriber's marginal token is **free**. A tool whose
  headline is "saves tokens" has no value proposition to that user until they hit
  a limit.
- For API/enterprise users cost is real: "$13 per developer per active day and
  $150-250 per developer per month", 90% under $30/active day (code.claude.com
  /docs/en/costs). Take the top of that band: $250/month. memor's honest blended
  0.8-3.9% saves **$2 to $10 per developer per month**. That does not clear the
  bar for a procurement conversation, a security review, or even the annoyance of
  installing a proxy.
- The constraint that actually bites Max users is **rate limits**. Anthropic
  announced weekly rate limits on 2025-07-28, effective 2025-08-28, estimating
  fewer than 5% of subscribers affected. There is now a class action (Kahn v.
  Anthropic, reported 2026-06) alleging Max usage falls short of advertised 20x
  and 5x multipliers. I read this via secondary reporting (techtimes.com,
  dataconomy.com, both 2026-06) and have **not verified the filing itself**;
  treat the lawsuit as reported-but-unverified.

  Rate limits are the one path by which token savings become subscriber value:
  fewer tokens means more turns before the weekly cap. But at 0.8-3.9% blended,
  memor buys roughly one extra hour in a 100-hour week. That is not a purchase
  decision.

### 4.2 What IS the binding constraint

Ranked by how directly the evidence supports it:

1. **Context window exhaustion mid-task.** Anthropic's own best-practices page
   leads with it: "Most best practices are based on one constraint: Claude's
   context window fills up fast, and performance degrades as it fills. The
   context window is the most important resource to manage." Their 100-turn eval
   describes workflows that "would otherwise fail due to context exhaustion".
2. **Compaction quality loss.** This is the highest-value unsolved problem and I
   could not verify it well. Auto-compact fires at the configured window and
   replaces detail with a summary; whatever the summary drops is gone. Claude
   Code's `/usage` now flags "long context or cache misses" as behaviour flags,
   which implies Anthropic considers these user-visible pathologies. But I found
   **no primary source quantifying post-compaction quality loss**, and my
   attempts to search developer complaints were blocked by search-engine
   anti-bot challenges. This is the most important open question in the report.
3. **Re-explaining the codebase.** Supported indirectly but broadly: every
   competitor's marketing leads with it (Cognee: "recall past work, decisions,
   and fixes instead of relearning them every run"; OpenMemory: "auto-captures
   your coding preferences, patterns, and setup"), and Anthropic's answer,
   CLAUDE.md, is a manually maintained file whose own docs warn that bloating it
   makes Claude ignore instructions. That is a genuine unmet need: CLAUDE.md does
   not scale, and skills are on-demand but manually authored.

### 4.3 What developers install and keep

I could not obtain install-retention data for any of these tools, and I will not
infer retention from GitHub stars. What I can say from primary sources:

- Distribution has converged on **plugins and hooks inside the harness**, not
  proxies. Honcho ships a Claude Code plugin and a Codex plugin. claude-mem ships
  hooks for six harnesses. Headroom ships `headroom wrap claude`. Cognee ships
  MCP. The proxy is the least-adopted surface even among vendors who offer it.
- Anthropic now attributes plan usage **per MCP server** in `/usage`, showing
  each server's share of consumption. A memory layer that adds tokens will now be
  visibly indicted by the harness itself. Any third-party memory tool must be
  net-negative on tokens *including its own retrieval payload*, or `/usage` will
  show the user exactly who to uninstall.

---

## 5. What would kill this product

Directly, as asked.

1. **Anthropic shipping an opinionated default memory implementation in Claude
   Code.** They have already shipped the protocol, the SDK helpers, and a
   reference local-filesystem handler. The remaining step is a curation policy.
   If Claude Code ever ships "memory on by default", the memory half of memor is
   a preference, not a product. I have no evidence they intend to and will not
   speculate; I am noting that the distance from where they are to there is small
   and entirely within their control.
2. **Leading with compression.** The compression numbers are honest, defensible,
   and commercially fatal. 0.8% blended against a competitor advertising 60-95%
   and a platform advertising 84% is a losing slide, and the fact that memor is
   the only one measuring correctly does not fix that. The honesty is an asset in
   the docs and a liability in the pitch.
3. **The prompt cache eating the savings.** If real-world use shows that memor's
   rewrites cause cache misses whose cost exceeds the tokens saved, the feature
   is net negative and the measurement discipline memor is built on will be the
   thing that proves it.
4. **Retrieval that is worse than doing nothing.** Zep's finding that full
   context beat Mem0 on Mem0's own benchmark is the template for how these
   products fail. Injecting a wrong memory is worse than injecting none, because
   it costs tokens and misleads. memor's 95.0%/86.7% must hold on real sessions,
   not just LongMemEval.
5. **Headroom out-executing on the same idea.** Same mechanism, same surfaces,
   same local-first positioning, Apache 2.0, VC-backed (Foundation Capital,
   Vercel accelerator, Berkeley Xcelerator per their site), with a logo wall.
   Being right about the numbers does not beat being installed.

### The strongest version of the product, on this evidence

Drop compression from the headline and keep it as an implementation detail.
Lead with **cross-harness, local, measured memory**: the thing the platform has
deliberately left unimplemented, that the cloud competitors cannot do privately,
and that attacks the one constraint (re-explaining the codebase) that a Max
subscriber genuinely feels. Then win on the eval nobody else is running: not
"how many tokens did I save" but **"how much of what mattered survived
compaction"**. memor's `eval-retention` (96.2% kept vs 50.3% for truncation at
comparable budget) is closer to that eval than anything a competitor publishes,
and post-compaction quality loss is the loudest unaddressed pain in the space.

---

## Evidence

All read 2026-08-22.

**Platform**
1. https://www.anthropic.com/news/context-management — dated 2025-09-29; context editing + memory tool; 39%/29%/84% figures; "public beta"
2. https://docs.anthropic.com/en/docs/build-with-claude/context-editing — strategies, `context-management-2025-06-27` beta header, cache invalidation, `clear_at_least`, client-side SDK compaction
3. https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/memory-tool — `memory_20250818`, all Claude 4+, client-side, `BetaLocalFilesystemMemoryTool`, "memory lives entirely in your application"
4. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents — published 2025-09-29; cites context rot; attention budget; just-in-time retrieval
5. https://docs.anthropic.com/en/docs/claude-code/costs and https://code.claude.com/docs/en/costs — $13/dev/active day, $150-250/dev/month, 90% under $30/day; Max/Pro session cost "isn't relevant for billing"; auto-compact; per-MCP-server usage attribution; long-context/cache-miss behaviour flags
6. https://docs.anthropic.com/en/docs/claude-code/best-practices — "context window fills up fast, performance degrades as it fills... the most important resource to manage"; CLAUDE.md bloat warning
7. https://code.claude.com/docs/en/model-config — auto-compact window configuration; 1M-context aliases
8. https://developers.openai.com/api/docs/guides/compaction — OpenAI compaction as a documented core concept
9. https://cursor.com/changelog/1-0 — dated 2025-06-04; Memories shipped as beta, per-project, per-individual
10. https://cursor.com/docs/context/memories — resolves to Cursor Rules docs; the dedicated Memories doc URL 404s. Memories-vs-Rules distinction **not fully verified from primary source**

**Competitors**
11. https://github.com/headroomlabs-ai/headroom and https://www.headroomlabs.ai/ — 60-95% JSON / 15-20% coding agents; library/proxy/MCP/wrap; Apache 2.0; local-first, reversible. Star count inconsistent between the two pages, unverified
12. https://github.com/thedotmack/claude-mem — capture, AI-compress, reinject; Claude Code / OpenClaw / Codex / Gemini / Copilot / OpenCode
13. https://www.letta.com/pricing — $0 / $20 Pro / API $20 + $0.10 per active agent/mo + $0.00015/sec tools / $20 seat / Enterprise
14. https://github.com/letta-ai/letta — V1 memory server archived; product is now `letta-code`, a coding agent harness
15. https://arxiv.org/abs/2310.08560 — MemGPT, virtual context management, submitted 2023-10-12
16. https://mem0.ai/pricing — Free / $19 / $249 / Enterprise; metered on add and retrieval requests
17. https://mem0.ai/openmemory — MCP memory for coding agents, auto-capture, per-project
18. https://arxiv.org/abs/2504.19413 — Mem0 paper, 2025-04-28; 26% over OpenAI on LOCOMO, 91% lower p95, >90% token saving
19. https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/ — published 2025-05-06, updated 2026-06-03; Zep 75.14% vs Mem0; LOCOMO flaws; **full-context baseline ~73% beat Mem0's ~68%**
20. https://www.getzep.com/pricing — Flex $1,250/yr, Flex Plus $3,750/yr, credit-per-350-bytes-episode, Cloud/BYOK/BYOC
21. https://github.com/getzep/graphiti — temporal context graph, validity windows, episodes as provenance, MCP server
22. https://www.cognee.ai/ — OSS graph memory, `pip install cognee`, Claude Code / Codex / MCP integrations, Cognee Cloud
23. https://supermemory.ai/pricing — $0/$19/$100/$399, SM-token metering, self-host on Scale+
24. https://honcho.dev/ — Neuromancer model, 60-90% token savings claim, LoCoMo 89.9% / LongMem S 90.4%, $0.001-$0.50 per query tiers, Claude Code and Codex plugins

**Research**
25. https://research.trychroma.com/context-rot — Chroma, 2025-07-14, 18 models, non-uniform degradation with length, task complexity held constant
26. https://arxiv.org/abs/2502.05167 — NoLiMa, ICML 2025; 11/13 models below 50% of baseline at 32K; GPT-4o 99.3% to 69.7%
27. https://arxiv.org/abs/2404.06654 — RULER, COLM 2024; only half of 32K-claiming models hold up at 32K

**Reported but not verified from primary source**
28. Weekly rate limits announced 2025-07-28, effective 2025-08-28, "<5% of subscribers" — read via news.creeta.com and apidog.com summaries, **not** found on an Anthropic page during this session
29. Kahn v. Anthropic class action re: Max usage multipliers — techtimes.com 2026-06-15, dataconomy.com 2026-06-16; filing not read

---

## Open questions

1. **How much quality is actually lost at compaction?** The highest-value
   unanswered question. No primary source found. Two search attempts were
   blocked by DuckDuckGo anti-bot challenges. If memor can measure this on its
   own session corpus, it owns a metric nobody else publishes.
2. **Does memor's rewriting cause net-negative cache behaviour in practice?**
   Answerable locally from the 5,414 proxied requests: compare cache-read tokens
   with and without engagement.
3. **Is there any measurable task-success difference at N vs 0.93N tokens?** If
   no, the quality argument for compression should be retired rather than
   defended.
4. **Retention data for any competitor.** Not obtainable from public sources.
   npm/PyPI download decay curves would be a proxy worth computing.
5. **Cursor Memories current behaviour.** Shipped beta 2025-06-04; the dedicated
   docs page 404s and content now lives under Rules. Whether Memories graduated,
   merged into Rules, or was withdrawn is **unresolved**.
6. **Anthropic's beta-to-GA status for context editing.** Docs still require the
   `context-management-2025-06-27` beta header while also saying "available on
   all supported Claude models". Announced, widely available, but by the strict
   reading **not declared GA**.
7. **Headroom's actual traction.** Star counts conflict between their own two
   properties. Funding claims are self-reported on their landing page.

## Confidence

| Claim class | Confidence | Why |
|---|---|---|
| Platform features, dates, mechanisms | **High** | Primary vendor docs and dated announcements |
| Competitor pricing and mechanism | **High** | Primary pricing pages read today |
| Competitor traction | **Low** | Self-reported, internally inconsistent |
| Long-context degradation research | **High** | Three peer-reviewed/major-lab sources agreeing |
| "7% saving does not matter for quality" | **Medium** | Inference from effect sizes; no direct experiment exists |
| "Token cost is not the binding constraint on Max" | **Medium-high** | Anthropic's own docs say session cost is not billing-relevant for Max; the rest is arithmetic |
| Ranking of real binding constraints | **Medium** | Constraint 1 well-sourced, 2 unsourced, 3 indirect |
| Anything about provider roadmaps | **Not assessed** | Out of scope by instruction |
