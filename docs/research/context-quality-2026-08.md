# What determines context quality for a coding agent

Research memo, read 2026-08-22. All URLs read on 2026-08-22 unless noted.
Purpose: inform the `memor` roadmap. Written to falsify the existing product
where the evidence points that way, not to validate it.

---

## Headline

**The highest-leverage direction is compaction integrity, not compression and not
general-purpose memory retrieval.**

Three independent lines of evidence converge on this:

1. Compaction is now a *measured* failure surface with published numbers, and the
   numbers are terrible: **17% retention of user-issued session constraints**
   across current compactors (COMPINT, arXiv 2608.11242), and **0% → 30% policy
   violation** induced purely by compaction (ConstraintRot, arXiv 2606.22528).
2. Token count is a weak lever on a coding-agent request. memor's own measured
   anatomy says tool results are only **33.0%** of a request and the blended
   saving is **0.8%** across 5,414 real proxied requests (`README.md`). That is
   arithmetic, not a weak compressor, and it caps the ceiling of the compression
   product.
3. Anthropic itself names the gap and does not close it. Their context-window
   docs publish a table of *what survives compaction*, and it says path-scoped
   rules and nested CLAUDE.md are **"Lost until a matching file is read again"**
   (docs.claude.com/en/docs/claude-code/context-window). That is a vendor-admitted
   hole in the mechanism, in the vendor's own documentation.

The class of knowledge that is genuinely unrecoverable by re-reading the repo is
narrow but real, and it maps almost exactly onto what compaction destroys:
in-conversation constraints, failed attempts, and rationale. That is the durable
niche. It is smaller than "agent memory" and more defensible.

---

## 1. Compaction quality

### It is a real, measured failure mode, not just developer grumbling

Until recently the case rested on anecdote. It no longer does. An arXiv sweep for
`"context compaction" AND agent` (export.arxiv.org API, 16 total results, read
2026-08-22) returns a small but sharply relevant literature that has appeared
almost entirely in the last ~14 months:

**COMPINT / "Lost in Compaction: Evaluating Side-Constraint Loss under Context
Compaction"** (arXiv:2608.11242, submitted 2026-07-31).
What was measured: retention of *Session Constraints* — user instructions like
"do not delete any emails until I confirm" — across three scenarios (multi-turn
chat, agentic trajectory, long-horizon research).
Result: **"Current compactors retain only 17% of injected SCs on average, and
most perform worse than running the same task without compaction."** Retention
varied with compactor, prompt, context length, SC phrasing and injection
location, which the authors read as evidence the loss is *systematic* rather than
setting-specific. Their fix is a plug-in SC-aware extractor running alongside the
compactor, reaching **>90% retention without modifying the compactor or the LLM**.

That last sentence is the most commercially important line in this memo. It is a
published demonstration that a **sidecar** — exactly memor's deployment shape —
closes an 17%→90% gap without touching the harness.

**ConstraintRot / "Governance Decay"** (arXiv:2606.22528v2, submitted 2026-06-21,
revised 2026-06-27). 1,323 episodes, seven model families, deterministic
tool-call grading.
Result: violation of an in-context governance constraint rises from **0% with the
policy in full context to 30% after compaction, reaching 59% for some models**.
The conditional decomposition is the crucial part: **when the constraint survives
the summary, violation stays 0%; when it is dropped, violation reaches 38%.**
That isolates the mechanism cleanly — the model is not getting dumber, the
constraint is simply gone. Their mitigation, "Constraint Pinning," quarantines
constraints from lossy compaction and **restores violation to 0%**, training-free.
They also demonstrate a *Compaction-Eviction Attack* where adversarial in-context
content biases the summarizer into omitting a legitimate policy, and report that
optimized injections **defeat every evaluated model**.

**"Parallel Context Compaction for Long-Horizon LLM Agent Serving"**
(arXiv:2605.23296, 2026-05-22) adds an operational observation that matters for
product design: *"the operator has no fine-grained control over summary volume
since prompt instructions are largely ignored, and as context grows, both the
amount of output tokens the model produces and the information it retains
fluctuate substantially from run to run, making the agent's retained knowledge
unpredictable across runs."* Compaction is not merely lossy, it is
**non-deterministically** lossy. Anthropic's own advice to use `/compact focus on
the auth bug fix` is, per this paper, partially ineffective.

**"Context Compaction Theory"** (arXiv:2608.01326, 2026-08-02, Mitzenmacher et
al.) gives the formal underpinning. It proves an equivalence between the "Context
Generation Game" (summarise into a bounded message) and **one-way communication
complexity**, so lower bounds transfer directly: for a given query set and error
target, there is a hard floor on compaction budget. It also proves that
*generation* can strictly beat *selection* on some query sets. Practical
implication for memor: a pure "keep these spans" selector is provably weaker than
an abstractive summariser on some workloads, so a serious compaction product
needs both. The paper includes a case study evaluating **Anthropic's context
compaction endpoint** on set-membership queries.

**CompactionRL** (arXiv:2607.05378, 2026-07-06) is the counter-evidence worth
taking seriously: it trains the model to be good at compaction via RL, reporting
GLM-4.5-Air at **66.8% Pass@1 on SWE-bench Verified (+7.0)** and **24.5% on
Terminal-Bench 2.0 (+3.1)**. This is a *model-side* fix. It suggests frontier
labs will eventually absorb part of this problem, which bounds how long a
harness-side sidecar stays valuable. It is also self-reported by the GLM team.

### Vendor-admitted holes

Anthropic's `context-window` doc publishes a table titled **"What survives
compaction"**:

| Mechanism | After compaction |
|---|---|
| System prompt and output style | Unchanged |
| Project-root CLAUDE.md and unscoped rules | Re-injected from disk |
| Auto memory | Re-injected from disk |
| **Rules with `paths:` frontmatter** | **Lost until a matching file is read again** |
| **Nested CLAUDE.md in subdirectories** | **Lost until a file in that subdirectory is read again** |
| Invoked skill bodies | Re-injected, capped at 5,000 tok/skill, 25,000 total, oldest dropped first |

Note what is *not* in the table: anything the user said in conversation. The
memory doc is explicit: *"If an instruction disappeared after compaction, it was
given only in conversation."* Anthropic's recommended fix is manual — go write it
in CLAUDE.md yourself. **That is a gap a tool can fill automatically and it is
the same gap COMPINT measured.**

### Developer complaints (anecdotal, but dense and consistent)

These are forum/issue reports, not measurements. Their value is in showing the
failure modes users actually notice match the ones the papers measure.

- anthropics/claude-code **#10948**, "Auto-compact triggers mid-task causing
  context loss and hallucinations" (opened 2025-11-04, closed as duplicate).
  Reported symptoms: *"Lose track of file modifications; Lose intermediate
  decisions and rationale; Lose specific technical details; Conflate what was
  planned vs what was actually implemented."* Note the second item — rationale —
  which is precisely the unrecoverable class in §4.
- **#10232** "[Bug] Auto-compact context summarization loses critical
  instructions" — specifically reports that a discovered workaround for a broken
  helper script was not retained, so the agent regressed to the broken approach.
  This is the *failed attempt / what-was-tried* class.
- **#4017** "[BUG] /compact causes Claude Code to ignore CLAUDE.md".
- **#13112**, **#27955** (plan context lost mid multi-phase execution),
  **#26532**, **#13068**, **#13043** (feature request: custom auto-compact
  prompt), **#9126**, **#65048**. The duplicate-closure chains on these are
  themselves evidence of volume.

Cursor: **could not verify.** DuckDuckGo served anti-bot challenges on repeated
attempts and I did not obtain primary forum.cursor.com threads. Do not cite
Cursor summarisation complaints without doing this search from a different
network.

### Is compaction the real pain point rather than token count?

Yes, and memor's own data is the strongest argument for it. From `README.md`:
tool-use arguments **42.7%** of a request (must not touch), tool results
**33.0%** (the only slice rewritten), conversation text **23.9%** (rewriting
reforms the cached prefix). At 11.7% saving across all tool output the whole
request falls **3.9%**; the blended figure over 5,414 real proxied requests is
**0.8%**. Meanwhile the compaction literature is reporting **17% vs 90%**
retention and **30–59%** induced violation rates. One of these is a
single-digit-percent efficiency lever; the other is a correctness lever with
double-digit swings.

There is also a systems datapoint: GitHub Copilot production traces (3.2M users,
13M sessions, 761M LLM calls, 95T tokens, June 2026; arXiv:2608.00101) report KV
cache hit rates of **~90% within a turn, falling to 55% across turn boundaries
and "drastically invalidated" after model switches or context compaction**. So
compaction is *also* the moment the economics get worst. A compaction event is
simultaneously the highest-risk correctness moment and a large cache-invalidation
cost event.

---

## 2. Context rot / degradation

The claim "performance degrades as context fills, independent of the limit" is
well supported. Quantifying a *fill fraction* threshold is where it gets slippery,
and honesty here matters.

**Lost in the Middle** (Liu et al., TACL 2023, arXiv:2307.03172). Multi-document
QA and key-value retrieval. Finding: performance is highest when relevant info is
at the **beginning or end**, and *"significantly degrades when models must access
relevant information in the middle of long contexts, even for explicitly
long-context models."* This is a *position* effect, not a fill-fraction effect.

**RULER** (Hsieh et al., COLM 2024, arXiv:2404.06654). 17 models, 13 tasks,
synthetic, with multi-hop tracing and aggregation categories beyond retrieval.
Finding: *"While these models all claim context sizes of 32K tokens or greater,
only half of them can maintain satisfactory performance at the length of 32K."*
RULER introduced the useful operational notion of an **effective** context length
far below the advertised one.

**NoLiMa** (Modarressi et al., ICML 2025, arXiv:2502.05167). NIAH with minimal
lexical overlap, forcing latent association. 13 models claiming ≥128K.
Finding: **at 32K, 11 of 13 models drop below 50% of their short-context (<1K)
baseline. GPT-4o falls from 99.3% to 69.7%.** NoLiMa's GitHub defines *effective
length* as the longest context retaining ≥85% of base score — a good metric to
steal. Caveat the Chroma authors raise: **72.4% of NoLiMa needle-question pairs
require external world knowledge**, so it partly measures knowledge, not purely
long-context handling.

**Chroma, "Context Rot"** (technical report, 2025-07-14, 18 models incl. GPT-4.1,
Claude 4, Gemini 2.5, Qwen3; 194,480 LLM calls). This is a vendor (a vector DB
company, with an obvious interest in the conclusion "don't stuff context, use
retrieval") but the methodology is unusually careful and the code is public.
Its design contribution is holding task complexity constant while varying only
input length. Findings relevant to memor:

- Degradation with input length is **universal across all 18 models**, on tasks
  as trivial as replicating a repeated-word sequence.
- **Distractors are the dominant lever, and they compound.** A *single* distractor
  reduces performance versus a needle-only baseline; four compound it further.
  Distractor impact is **non-uniform** — some distractors are far worse than
  others — and the effect *amplifies with input length*.
- **Claude models abstain rather than hallucinate** under ambiguity; GPT models
  hallucinate confidently. For a coding agent this is a behavioural difference
  worth designing around.
- **LongMemEval focused vs full**: with ~113k-token full inputs versus ~300-token
  focused inputs containing only the relevant spans, **all models scored
  significantly higher on focused**. Chroma frame this as: full input forces the
  model to do retrieval *and* reasoning in one call; focused input lets it just
  reason. The Claude family showed the largest gap, driven by abstention.
- Counterintuitive and worth knowing: **shuffled haystacks beat coherent ones**,
  consistently across all 18 models and configurations.

**At what fill fraction does degradation begin?** Honest answer: **no source
supports a clean fill-fraction threshold, and I would not put one in a product
claim.** What the evidence supports is *absolute-length* thresholds and *content*
effects:
- Measurable degradation on semantically-indirect retrieval is clear by **32K
  absolute tokens** (NoLiMa: 11/13 models below half their baseline; RULER: half
  the models unsatisfactory at 32K).
- Chroma observe non-uniform degradation from the very shortest lengths tested;
  there is no knee, it is *"a performance gradient rather than a hard cliff"*
  (Anthropic's phrasing of the same point).
- The 85%-of-baseline "effective length" definition from NoLiMa is the most
  defensible operationalisation available.

Anthropic endorse the phenomenon in their own engineering blog, citing Chroma
directly, and give the mechanistic account: n² pairwise attention relationships
stretched thin, plus training distributions where short sequences dominate, so
models have *"fewer specialized parameters for context-wide dependencies."*

**The actionable synthesis for memor:** the strongest measured effect in the
context-rot literature is not raw length, it is **distractors**, and distractor
harm grows with length. A memory system that injects a plausible-but-wrong past
memory is *manufacturing a distractor* at the exact position and length where
Chroma measured them to be most damaging. This is a real risk to the retrieval
product and should be evaluated for explicitly (see §5).

---

## 3. What frameworks do today

The convergent design across every major harness is: **a small always-loaded
instruction file, plus just-in-time retrieval, plus sub-agents for context
isolation, plus an external file scratchpad.** Nobody has solved compaction.

**Anthropic / Claude Code.** The primary text is *"Effective context engineering
for AI agents"* (2025-09-29). Key positions:
- Goal is *"the smallest possible set of high-signal tokens."* Context is an
  "attention budget."
- **Just-in-time over pre-loading**: agents hold lightweight identifiers (file
  paths, queries) and load at runtime. Explicitly: Claude Code uses glob and grep
  to retrieve just-in-time, *"effectively bypassing the issues of stale indexing
  and complex syntax trees."* This is a direct shot at embedding-index approaches
  like Cursor's.
- They describe Claude Code as a **hybrid**: CLAUDE.md pre-loaded, everything
  else JIT.
- On compaction they state the agent continues *"with this compressed context plus
  the five most recently accessed files"* and warn that *"overly aggressive
  compaction can result in the loss of subtle but critical context whose
  importance only becomes apparent later."* Their tuning advice is
  **maximise recall first, then improve precision** — a directly usable recipe.
- Note a subtlety they flag: the *safest lightest-touch* compaction is **tool
  result clearing**, shipped as a Claude Developer Platform feature. This is
  adjacent to memor's compression product and is a competitive threat to it.
- Structured note-taking / NOTES.md, and the Claude Playing Pokémon example of
  cross-reset coherence.
- Sub-agents: each may burn tens of thousands of tokens and return
  **1,000–2,000 tokens** distilled.

Claude Code shipped **auto memory** (docs.claude.com/en/docs/claude-code/memory),
which is close to memor's memory product and worth studying as the competitive
baseline. Design details that look load-bearing:
- Four types: `user` (role, preferences), `feedback` (corrections you gave),
  `project` (ongoing work, decisions **"that Claude can't derive from the code or
  git history"**), `reference` (where to find external info).
- Explicitly: *"Claude skips anything it can derive from the codebase, such as
  architecture, file paths, or debugging fixes."* **Anthropic have independently
  arrived at the §4 thesis and encoded it as a write-filter.**
- `MEMORY.md` index loaded every session, capped at **200 lines / 25KB**; topic
  files loaded on demand. A two-tier index/detail split.
- Auto memory **is re-injected from disk after compaction**. That is the
  mechanism that makes file-based memory compaction-proof, and it is the
  architectural lesson.
- Machine-local, plain markdown, user-auditable via `/memory`.

Related Anthropic material: *How we built our multi-agent research system*
(2025-06-13) reports the multi-agent system **outperformed single-agent Opus 4 by
90.2% on their internal research eval**, that **token usage alone explains 80% of
performance variance** on BrowseComp, and the cost: agents use ~4× chat tokens,
multi-agent ~15×. They also caution *"most coding tasks involve fewer truly
parallelizable tasks than research."* Their appendix recommends **subagents write
to a filesystem rather than passing results through the coordinator**, to avoid a
"game of telephone" — i.e. summarisation loss again.

**Cursor.** Rules in `.cursor/rules` as `.mdc` files with frontmatter
(`alwaysApply` / `description` / `globs`), giving four activation modes: always,
agent-selected by description, glob-attached, manual @-mention. Plus Team Rules,
User Rules, and nested `AGENTS.md`. Their own best practice is notable and aligns
with §4: *"Reference files instead of copying their contents — this keeps rules
short and prevents them from becoming stale as code changes"*, and *"Duplicating
what's already in your codebase: point to canonical examples instead of copying
code."* Cursor also maintains an embedding index of the codebase, which is the
position Anthropic argue against. **I could not verify Cursor's memories feature
details or summarisation complaints** — the docs URL for memories redirected to
the Rules page.

**Cline.** Memory Bank is the purest expression of file-based memory: six
markdown files (`projectbrief`, `productContext`, `activeContext`,
`systemPatterns`, `techContext`, `progress`) with an explicit manual protocol —
"update memory bank" → new conversation → "follow your custom instructions". The
framing in their own custom-instruction text is telling: *"my memory resets
completely between sessions... After each reset, I rely ENTIRELY on my Memory
Bank."* They treat context-window exhaustion as a **planned handoff** rather than
an interrupt, which is arguably the correct design and the opposite of
auto-compact.

**Aider.** The repo map: a tree-sitter-derived map of classes/functions/signatures
across the whole repo, ranked by a **graph ranking algorithm** over a
file-dependency graph, fitted to a token budget defaulting to **1k tokens**. This
is the cheapest and oldest of the just-in-time designs and still a strong
baseline: a 1k-token structural index that tells the model *which file to read*.

**Codex CLI / AGENTS.md.** Could not verify primary docs in this pass.

**Counterpoint — Cognition, "Don't Build Multi-Agents"** (2025-06-12). Two
principles: *share full agent traces, not just individual messages*; and *actions
carry implicit decisions, and conflicting decisions carry bad results*. Directly
contradicts Anthropic's sub-agent enthusiasm. Their recommended architecture for
long tasks is single-threaded plus **a dedicated compression model**, and they
note they have **fine-tuned a smaller model for exactly this** at Cognition:
*"This is hard to get right. It takes investment into figuring out what ends up
being the key information."* Independent confirmation from a serious shop that
compaction quality is the bottleneck and is worth a dedicated model.

**"Inside the Scaffold"** (arXiv:2604.03515v2) analysed 13 open-source coding
agent scaffolds at pinned commits and found **context compaction spans seven
distinct strategies**, listing it among the dimensions where designs *diverge*
because *"open design questions remain."* An unsettled design space is where a
focused tool can win.

---

## 4. Retrieval vs recomputation

**The default should be recomputation.** Files are ground truth, cheap to read,
and never stale. Anthropic's JIT argument is that grep/glob *"bypass the issues of
stale indexing"*; Cursor's own rules guidance says point at canonical examples
rather than copying them. A memory system that stores facts derivable from the
repo is building a stale cache of a cheap, authoritative source — strictly worse
than a read, and per §2 it is worse than nothing because a stale memory is a
**distractor**.

So the question is only: what is genuinely not in the repo?

**Not recoverable by reading the repo:**

1. **Why a decision was made.** Code records *what*, rarely *why*. The
   architecture-decision-record tradition exists precisely because rationale is
   not naturally captured, and the honest read of that literature is that
   rationale capture has a long history of falling into disuse because it is
   manual. An agent that captures rationale as a byproduct of the work removes
   the discipline cost, which is the historical failure cause. *(I have not
   verified a specific citation for the ADR adoption-failure claim — treat as
   informed prior, not established fact.)*
2. **What was tried and failed.** Strictly absent from a repo by construction: a
   failed approach leaves no artifact, or worse, was reverted and leaves a
   misleading absence. This is the highest-value class. Issue #10232 is a real
   instance: a discovered workaround for a broken helper script was lost to
   compaction and the agent regressed to the broken approach. **Negative
   knowledge is unrecoverable at any price and is re-derived at full cost every
   time it is lost.**
3. **User preferences and corrections.** Anthropic's `feedback` memory type.
   Recoverable only from conversation.
4. **Cross-session debugging state.** "The flake only reproduces under
   `-p no:randomly` on this machine." Environment-conditional and machine-local.
5. **Tacit environment knowledge.** Anthropic's `reference` type: which dashboard,
   which issue tracker.

Anthropic converged on the same taxonomy and turned it into a **write-side
filter** ("skips anything it can derive from the codebase"). This is the single
most transferable design decision in this memo: **the discipline is at write
time, not read time.** Most memory systems are recall-optimised; the binding
constraint is precision, because a wrong memory is a distractor and §2 says
distractors are the most damaging thing you can put in a context window.

**Where retrieval beats reading even for repo-derivable facts:** when the read is
*expensive to locate*, not expensive to perform. Aider's repo map is the honest
version of this — it is an index of *where to look*, not a cache of *what is
there*, so it cannot go stale in a harmful way (a wrong pointer costs one wasted
read; a wrong fact costs a wrong action).

**The Letta result is the sharpest challenge to specialised memory tooling.**
Letta (2025-08-12) put the LoCoMo conversation history into a plain file, gave a
GPT-4o-mini agent `grep`, `search_files`, `open`, `close`, and scored **74.0%**,
above Mem0's reported **68.5%** for their best graph variant. Their conclusion:
*"Agents today are extremely effective at using filesystem tools, largely due to
post-training optimization for agentic coding tasks... simpler tools are more
likely to be in the training data of an agent and therefore more likely to be
used effectively."* Note the self-interest on both sides — Letta compete with
Mem0 — but the direction of the bias is against the more complex system, and it
is the cheaper hypothesis.

**Implication for memor:** a bespoke retrieval engine competes with `grep` over
markdown, and `grep` has the advantage of being in the model's post-training
distribution. The defensible value is in **what gets written and when**, not in
the retrieval mechanism. Retrieval sophistication is likely over-invested.

---

## 5. Evaluation

### The existing benchmarks

**LongMemEval** (Wu et al., ICLR 2025, arXiv:2410.10813). 500 curated questions
over scalable chat histories, five abilities: information extraction,
multi-session reasoning, temporal reasoning, knowledge updates, **abstention**.
Headline: *"commercial chat assistants and long-context LLMs showing a 30%
accuracy drop on memorizing information across sustained interactions."* Their
indexing/retrieval/reading decomposition is a good framework. The abstention
category is underrated and directly measures the distractor risk from §2.

**LoCoMo** — used everywhere, and the critiques are substantial. Letta could not
determine how Mem0 ran MemGPT on it, Mem0 **did not respond** to requests for
clarification (mem0ai/mem0 issue #3004), and the same team that built MemGPT says
backfilling LoCoMo into it would need significant refactoring. Treat *any*
LoCoMo number from a memory vendor as unverified. Letta's broader methodological
point stands regardless: *"Comparing agent frameworks and agent memory tools is
like comparing apples to oranges"* — memory performance is confounded by the
agent's tool-calling ability and the model, so a memory benchmark that does not
hold framework and model constant measures the wrong thing.

memor already reports LongMemEval_S at **95.0% any-hit / 86.7% all-gold, n=120**
against published ground truth. That is a respectable, falsifiable retrieval
number. **But it is a conversational-QA benchmark, and it does not measure
anything in §1 or §4.** Scoring well on it is not evidence that memor helps a
coding agent.

### What a coding-specific memory benchmark should measure

The gap is stark: there is a rigorous compaction literature (COMPINT,
ConstraintRot) and a rigorous conversational-memory literature (LongMemEval), and
**nothing joins them for coding agents**. That is the opening.

Proposed measurements, in descending order of value:

1. **Constraint survival across compaction.** Directly the COMPINT metric,
   instantiated on coding sessions: "never touch `migrations/`", "we use pnpm",
   "don't run the full suite, it takes 40 minutes". Measure retention rate and,
   crucially, **downstream violation rate** — ConstraintRot showed these are
   different numbers and the conditional (violation *given* the constraint was
   dropped) is the informative one.
2. **Negative-knowledge retention.** Does the agent re-attempt a documented failed
   approach after a compaction boundary? This is the class no repo read can
   recover, and issue #10232 is a naturally-occurring instance. Constructing this
   as a benchmark is novel as far as I found.
3. **Distractor harm / precision under injection.** Inject a *stale* memory (true
   last week, false now) and measure whether the agent follows it over the file.
   This measures the downside risk of the memory product and almost nobody
   reports it. Pair with an abstention metric à la LongMemEval.
4. **Recomputation baseline, always.** Every memory result must be reported
   against "the agent just greps and reads." Letta's filesystem result is the
   warning: if the baseline is not run, the benchmark flatters the tool.
5. **End-state, not trajectory.** Anthropic's appendix is right that agents reach
   goals by different valid paths; grade the final state at discrete checkpoints.
6. **Long-horizon task success**, e.g. Terminal-Bench-style, as the outer metric.
   The compaction papers ground themselves in SWE-bench Verified and Terminal-Bench
   2.0, so those are the venues where a compaction claim will be believed.

An honest name for the thing to build: **a compaction-integrity benchmark for
coding agents**, with the memory system as one candidate intervention among
several (constraint pinning, tool-result clearing, sidecar extraction).

---

## Recommendation for the roadmap

Ranked by measured effect size × defensibility:

1. **Compaction sidecar / constraint pinning.** Detect user-issued session
   constraints, decisions, and failed attempts in-flight; persist them to disk;
   re-inject after every compaction boundary. Two published papers show this
   shape works (17%→>90%, 30%→0%) and both mitigations were **training-free and
   external to the harness**. Anthropic's own docs name the hole. memor is
   already a proxy/hook sitting in the right place to see the traffic.
2. **Write-side precision discipline.** Adopt Anthropic's filter explicitly:
   never store what a repo read recovers. Store rationale, negative results,
   preferences, environment state. This makes memor's memory complementary to
   `grep` rather than competing with it.
3. **A coding-specific compaction-integrity benchmark.** No one owns this. It
   would make memor's claims falsifiable in the venue where the audience already
   is (SWE-bench / Terminal-Bench), and the README's existing culture of
   publishing unflattering denominators is a genuine differentiator here.
4. **Compression: maintain, do not invest.** memor's own arithmetic caps it at
   ~3.9% of a whole request, Anthropic ship tool-result clearing natively, and
   the Copilot trace paper shows cache behaviour dominates the economics anyway.
   It is a good credibility artifact and a poor growth vector.
5. **Retrieval sophistication: deprioritise.** Letta's filesystem result plus
   Anthropic's anti-index position suggest returns here are thin.

---

## Open questions

- **No fill-fraction threshold exists in the literature.** Everything is absolute
  length or position. Is there a *measurable* knee for coding-agent contexts
  specifically? memor has the traffic to answer this and nobody has published it.
- **Cursor: entirely unverified.** Anti-bot blocking prevented forum access.
  Cursor's memories feature and its summarisation complaints are a hole.
- **Codex CLI / AGENTS.md compaction behaviour: not verified.**
- **How long does the sidecar niche last?** CompactionRL shows labs training
  compaction into the model. If GLM-5.2 ships with it and Anthropic follow, the
  harness-side fix has a shelf life. Counter-argument: Anthropic have shipped
  compaction for over a year and the docs still say path-scoped rules are lost.
- **Does memory injection measurably harm?** The distractor finding predicts it
  should sometimes. I found no memory vendor reporting a regression rate. That
  absence is itself informative and it is a cheap, honest thing for memor to
  publish first.
- **ADR/rationale-capture adoption failure** — asserted from prior, not verified
  against SE literature. Should be checked before appearing in any external
  writing.
- **COMPINT and ConstraintRot are recent preprints** (July 2026, June 2026) with
  limited citation history. Their headline numbers should be independently
  reproduced before being used in marketing. Both ship code.

---

## Evidence

| # | URL | Read | Supports |
|---|---|---|---|
| 1 | https://arxiv.org/abs/2608.11242 | 2026-08-22 | COMPINT: compactors retain 17% of session constraints; sidecar extractor reaches >90% |
| 2 | https://arxiv.org/abs/2606.22528 | 2026-08-22 | ConstraintRot: 0%→30% violation after compaction (59% worst model); 0% when constraint survives, 38% when dropped; pinning restores 0%; 1,323 episodes, 7 model families |
| 3 | https://arxiv.org/abs/2608.01326 | 2026-08-22 | Context Compaction Theory: equivalence to one-way communication complexity; generation can strictly beat selection |
| 4 | https://arxiv.org/abs/2605.23296 | 2026-08-22 | Compaction is non-deterministically lossy; prompt instructions "largely ignored"; operator lacks volume control |
| 5 | https://arxiv.org/abs/2607.05378 | 2026-08-22 | CompactionRL: model-side fix; GLM-4.5-Air 66.8% SWE-bench Verified (+7.0), 24.5% Terminal-Bench 2.0 (+3.1); self-reported |
| 6 | https://arxiv.org/abs/2608.00101 | 2026-08-22 | Copilot production traces: KV cache 90% in-turn → 55% cross-turn, "drastically invalidated" after compaction; 3.2M users, 95T tokens |
| 7 | https://arxiv.org/abs/2604.03515 | 2026-08-22 | 13 scaffolds analysed; context compaction spans 7 distinct strategies, an unsettled design dimension |
| 8 | https://www.trychroma.com/research/context-rot | 2026-08-22 | Context rot across 18 models; distractors compound and amplify with length; focused ≫ full prompts on LongMemEval; shuffled > coherent haystacks; Claude abstains, GPT hallucinates |
| 9 | https://arxiv.org/abs/2502.05167 | 2026-08-22 | NoLiMa: at 32K, 11/13 models below 50% of <1K baseline; GPT-4o 99.3%→69.7%; 72.4% of pairs need world knowledge (caveat) |
| 10 | https://arxiv.org/abs/2404.06654 | 2026-08-22 | RULER: only half of models claiming ≥32K perform satisfactorily at 32K |
| 11 | https://arxiv.org/abs/2307.03172 | 2026-08-22 | Lost in the Middle: position effect, mid-context degradation (TACL 2023) |
| 12 | https://arxiv.org/abs/2410.10813 | 2026-08-22 | LongMemEval: 500 questions, 5 abilities incl. abstention; ~30% accuracy drop for commercial assistants |
| 13 | https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents | 2026-08-22 | JIT vs pre-loading; attention budget; compaction preserves decisions/bugs + 5 recent files; maximise-recall-then-precision; tool-result clearing; subagents return 1–2k tokens |
| 14 | https://docs.claude.com/en/docs/claude-code/context-window | 2026-08-22 | "What survives compaction" table: path-scoped rules and nested CLAUDE.md lost; auto memory re-injected from disk; skills capped 5k/25k |
| 15 | https://docs.claude.com/en/docs/claude-code/memory | 2026-08-22 | Auto memory: 4 types; skips anything derivable from codebase; MEMORY.md 200 lines/25KB; conversation-only instructions lost after compaction |
| 16 | https://docs.claude.com/en/docs/claude-code/costs | 2026-08-22 | Hooks/skills/subagents as context reduction; agent teams ~7× tokens; compaction itself is a large request |
| 17 | https://github.com/anthropics/claude-code/issues/10948 | 2026-08-22 | Anecdotal: auto-compact loses file modifications, intermediate decisions and rationale; conflates planned vs implemented |
| 18 | https://github.com/anthropics/claude-code/issues/10232 | 2026-08-22 | Anecdotal: workaround for broken helper script lost to compaction, agent regressed (negative-knowledge instance) |
| 19 | https://github.com/anthropics/claude-code/issues/4017 | 2026-08-22 | Anecdotal: /compact causes CLAUDE.md to be ignored |
| 20 | https://www.letta.com/blog/benchmarking-ai-agent-memory | 2026-08-22 | Filesystem + grep with GPT-4o-mini scores 74.0% on LoCoMo vs Mem0's reported 68.5%; Mem0 did not respond to methodology queries; vendor source, self-interested |
| 21 | https://cognition.ai/blog/dont-build-multi-agents | 2026-08-22 | Share full traces; conflicting implicit decisions; Cognition fine-tuned a dedicated compression model |
| 22 | https://www.anthropic.com/engineering/multi-agent-research-system | 2026-08-22 | +90.2% vs single-agent on internal eval; token usage explains 80% of BrowseComp variance; 15× tokens; coding less parallelisable; subagents→filesystem to avoid telephone |
| 23 | https://aider.chat/docs/repomap.html | 2026-08-22 | Graph-ranked tree-sitter repo map, ~1k token default budget |
| 24 | https://cursor.com/docs/context/memories | 2026-08-22 | Cursor rules: 4 activation modes; "reference files instead of copying their contents"; memories page redirects to Rules — feature unverified |
| 25 | https://docs.cline.bot/prompting/cline-memory-bank | 2026-08-22 | Cline Memory Bank: 6-file structure, manual handoff protocol at context exhaustion |
| 26 | /Users/nimit/Documents/Projects/Memorable/README.md | 2026-08-22 | memor's own measured request anatomy: tool results 33.0%, blended saving 0.8% over 5,414 requests, ceiling 14.3%; LongMemEval_S 95.0%/86.7% |

**Confidence.** High on §1 (compaction is the leverage point) — two independent
peer-review-track preprints with code, a vendor-documented hole, and memor's own
arithmetic all agree. High on §2's qualitative claim, **low on any fill-fraction
number** — I decline to give one. Medium-high on §3, with Cursor and Codex
unverified. Medium-high on §4: the taxonomy is well supported and independently
converged on by Anthropic, but the ADR sub-claim is unverified prior. Medium on
§5 — the benchmark design is my synthesis, not a citation.
