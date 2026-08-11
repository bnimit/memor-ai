# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.13.0] - 2026-08-10

Upgrade if you use Claude Code with a subscription (not an API key) and ever ran
`memor install-proxy --agent claude`: recall was silently disabled for you, and
this release restores it. No action beyond upgrading is needed.


### Added
- **A/B harness for older-turn compression, measured net of cache** (`memor/eval/older_turns_ab.py`, disabled by default). The proxy compresses only the newest turn, which reaches **0.09% of the compressible tool output in a request** — measured across 96 real conversation states, 24,895 tokens visible against 26,384,399 available. On one real session `extract_all_tool_payloads` finds 1,410,143 tokens and the function the pipeline actually calls finds 0. That is the explanation for a lifetime compression rate of 0.77%, and for the 2,513 requests logged with `content_types={}`: in 11% of states the newest turn contains no tool payload at all, so nothing is offered to classify. The fix looks obvious and is not: rewriting an older turn rewrites the cached prefix, and Anthropic bills a cache write at 1.25x against a cache read at 0.1x. This machine's ledger is **99.7% cache reads** (165,974,863 of them), and replaying the currently-skipped payloads through the real compressor saves 10.7% gross. **Break-even is 9.3%** — the change may invalidate at most that share of cached tokens before it loses money, so the margin is thinner than the saving it chases. The harness assigns the wider window per conversation (stably hashed, so no conversation flips arms mid-flight and pays for its own switch), compares billed input units rather than raw tokens, and reports gross and net side by side so a net loss is legible rather than surprising. Tests lead with the negative case: a treatment arm with better gross savings and worse billing must be reported as `costs`.

- **Output-token shaping (`MEMOR_OUTPUT_SHAPER=1`, off by default, not yet wired into the proxy).** Output is 43% of spend on real traffic here (68,541 output tokens against 461,385 input, billed at 5x), and no compressor touches it because it does not exist when the request is sent. The only lever is asking the model to write less, which is dangerous, so this is deliberately narrower than the competing implementation. **Effort routing is not implemented at all**: its natural trigger, "this turn is just resuming after a tool result", fires on 93% of assistant turns because 92% of user-typed records *are* tool results, and 28% of those follow a tool result carrying an error signal — precisely the turn where an agent reads a failing test and must work out why. Verbosity steering is implemented and suppressed whenever the turn could produce code: the conversation has used a write tool, the last tool result contains an error signal, or write tools are available and the prompt asks for a change. The note itself forbids abbreviating code. Replayed over 9,635 real assistant turns the gates decline 73% (58.7% code-writing, 14.6% post-error) and protect 67% of prose characters. A holdout arm (10% by default, hashed per conversation so assignment is stable) leaves the saving measurable rather than counterfactual. **Measured ceiling: 0.9–2.3% of total spend**, which is why it stays off and unwired pending a decision on whether that is worth any risk to shipped code.

- **Provenance page on the dashboard** (`/api/provenance` and a Provenance tab). Shows how raw transcript becomes durable knowledge, using the edges memor actually records: a memory linked to the session chunks it was distilled from, and to the memory it replaced when a fact changed. A floating force-directed graph: linked nodes pull together so a memory sits among the chunks it came from, and the best-connected nodes carry a readable label taken from their own text rather than being anonymous dots. Labels skip instruction preamble and section headings ("You are doing a...", "## Context") and fall through to the first line that says something, because a node labelled with boilerplate is a dot with extra steps. The simulation keeps running rather than being computed once and painted, so the graph drifts and settles on screen and a node dragged by the user pulls its neighbours; each node carries a slow orbit of its own, because a pure force simulation stops dead once the forces balance and reads as a still image. Spacing is measured rather than eyeballed: the default leaves a 73px median gap between neighbours across 82% of the canvas, and a label is suppressed when it would overlap one already placed. An inferred entity graph over shared identifiers was measured first and rejected: 1-hop expansion added a median of 12 candidates for 8 slots, which is more ranking work rather than better retrieval, and 33,883 identifiers over 27,218 artifacts renders as a hairball whose readable version is a subset chosen for looks. Every line drawn here was written by the distiller at ingest and traces back to the text that produced it. Layout is a pure function so it can be executed under node in tests rather than reviewed by eye, which is how the previous panel's locale bug (2,343,748 rendered as "23,43,748") was caught.

- **Distilled memories now get a reserved share of the recall slots, and transcript chunks that merely quote the question are demoted.** Memories are 9.3% of active artifacts but were reaching only 3.0% of injected hits, a 0.33x lift, meaning the distillation pipeline was writing knowledge that retrieval then declined to use. The likely explanations were ruled out first: the embedding does not disfavour prose (cosine to real prompts is 0.275 for memories against 0.273 for chunks), the stored vectors are correct (self-similarity 1.0000), index coverage is 100% for both kinds, and distillation is still running daily. What remains is competition, and part of it is illusory: 35% of top hits contained more than half the query verbatim, because a prompt drawn from a live session is near-duplicate text to the transcript chunk that recorded it. A paraphrase cannot out-score a copy of the question, which is why raising the kind boost from 1.3 to 3.0 only moved the share 2.8% to 4.1%. Memories now compete against each other for 25% of k, and chunks above 60% verbatim containment are demoted below genuine matches (demoted, not dropped, because a chunk that quotes the prompt often continues into the answer). The lane also runs its own kind-restricted search rather than only re-ranking the general pool, because the pool usually contains no memory to promote: memories are 4.2% of the dense top-8 and 4.1% of the top-32, so widening does not help, while a dedicated search surfaced candidates for 38 of 60 real queries that the general pool never returned. Admission uses a relative floor (25% of the best general hit) so the lane cannot buy share with padding. Measured over 60 real prompts on the real store: **memory share 3.5% to 7.7%, lift 0.38x to 0.83x**, and promoted candidates beat the ones they displaced by true cosine (0.548 against 0.519), so the extra slots are earned rather than filled. LongMemEval is unchanged at 95.0% any-hit and 86.7% all-gold, confirming general retrieval did not regress. The lane is a floor rather than a quota: unfilled slots return to the general pool, so k is never padded with weak results.

- **`memor eval-longmemeval`** scores retrieval against LongMemEval, the published benchmark for long-term chat memory (ICLR 2025). Every eval in this repo until now ran through an LLM judge, and a judge measures the judge as much as the system: on the same project with the same config, the counterfactual win rate read 63.8% before the harness was corrected to call production `recall()` and 8.6% after, and nothing about retrieval had changed in between. LongMemEval ships ground truth — each question names the sessions holding its evidence — so the score is set membership, with no model in the loop, no API key and no cost. Measured on the S split (~48 sessions and ~491 turns of distractors per question), stratified across all six memory abilities: **95.0% any-hit and 86.7% all-gold at k=8, n=120, 5ms p50**. Both numbers are reported because the lenient one flatters: any-hit is the paper's session-level recall, but multi-session and temporal questions need every evidence session to be answerable, and that gap is where the weakness is (temporal-reasoning 95% any-hit against 70% all-gold). Retrieval is scored, not end-to-end QA, so this is not comparable to the accuracy figures in the paper's leaderboard.

- **Tool-output compression for Claude Code** (`memor install-compress-hook`). A `PostToolUse` hook rewrites Bash output via `updatedToolOutput` before it enters the transcript. This is where the compressible mass actually is: by the time the proxy sees a request, most of it is conversation history the compressor must leave byte-exact, and the payload it *can* shrink entered the transcript once and is resent verbatim on every later turn. Crushing it on the way in also means no already-cached prefix is rewritten, so unlike the proxy path there is no cache re-formation cost to weigh against the saving. Measured on 1,219 real large Bash results from live sessions: it fires on 259 of them and saves 51.9% on those, 13.2% across all large Bash output. `memor hook-worth` re-derives this on your own transcripts and prints the denominator it used. Read, Grep and Glob results are excluded from that denominator rather than counted as missed coverage, because the hook declines them by design; an earlier figure of 38.6% mixed them in and so described a population the hook never receives. Scope is narrow by construction — Bash only, never Read/Grep/Glob, never source, and a non-zero exit passes through whole because a failing build is when every line matters most. Verified that assertion text, `file:line`, the `FAILED` line and the pass/fail summary all survive a compressed failing run.
- **Savings are now measured net of the provider's prompt cache.** Every `upstream_*` column in the ledger was NULL across all 5,414 rows of real traffic, and the cause was structural rather than a bug in one branch: usage arrives inside the response stream, and the streaming path wrote its ledger row before forwarding a single byte, so the columns were hardcoded to None with a comment saying usage was "not available for streaming". Agents stream essentially everything. `memor/proxy/usage.py` now sniffs SSE frames as they pass through to the agent without altering them, reassembling frames across chunk boundaries, and completes the row when the stream ends. Anthropic splits usage across `message_start` and `message_delta`; OpenAI counts cached tokens *inside* `prompt_tokens` where Anthropic reports them separately, so the two are normalized to one shape. `upstream_cache_creation_tokens` is a new column: rewriting a prefix that was being served from cache converts cheap cache reads into full-price writes, so a positive gross saving can be a net loss, and only the write count can show it.
- **`memor hook-worth`** replays the compression hook over your own session transcripts and reports what it saved, including the denominator it used. The figures published for this feature came from a throwaway script, and a number nobody can re-derive is a number nobody can check: two rates in this cycle turned out to describe the wrong population before anyone noticed.
- **`memor service status` reports a proxy running code older than what is installed.** memor is commonly installed editable, so the sources on disk move ahead of a long-running proxy every time the repo changes. Nothing surfaced that: the installed version reported the new number while the socket served the old behaviour, and a feature looked broken for a reason no log explained. `/health` now returns the loaded version, the process start time and a `captures_stream_usage` capability flag, and status compares that start time against the package mtime.

### Fixed
- **The CCR marker promised an original the model had no way to fetch.** The compression path writes `[memor:ccr:<id>]` above a shortened payload, telling the model that full content exists, but nothing on the proxy path declared a tool to retrieve it — `memor_retrieve` lives in a separate MCP server that a user may never have installed. A shortened payload with no way back is exactly the failure the source guard exists to prevent, so this gap is why that guard cannot be lifted. The proxy now declares the tool in the request itself, but only when a marker is actually present: a tool declaration costs tokens on every request and sits in the prefix providers cache, so advertising it universally would be a permanent tax for a case that cannot arise. It is appended last and never reorders the agent's own tools, because tool order is part of the cached prefix and rewriting it would turn cache reads into writes on a workload that is 99.7% cache reads.

- **The "does recall reduce work?" panel could not have detected an effect.** `tool_call_count` was the number of tool_use blocks in a single assistant message, not in a turn. Claude Code delivers each tool result as a record typed `user` -- 92% of `user` records in real transcripts are tool results, not prompts -- so every turn was split at its first tool call. The column could only ever be 0 or 1 (67%/33% across 107,066 rows), which is why the panel's median read 0.0 in both arms and could not move whatever recall did. Counted per real turn, the median is 4 tool calls and the maximum is 87. `is_user_prompt` already existed in `episodes.py` and did this correctly; `turn_metrics.py` never used it.
- **The recall-effect headline was mostly project mix.** The two arms are not drawn from the same projects: plirin is 42% of the recalled arm against 16% of the control, ygo 9% against 28%. Projects differ in how tool-heavy they are, so the pooled comparison partly measured which projects get recall rather than what recall does. On the development machine the naive aggregate read -33.3% where the episode-weighted within-project figure was -6.1% -- five sixths of the headline was composition. `project_adjusted_delta` now computes the difference inside each project and pools it on a common weighting, projects with fewer than 25 episodes per arm are excluded rather than estimated, and the verdict returns `no_effect` when projects disagree on direction, exactly as it already did for prompt-size strata.

- **`scope.kinds` could not retrieve a minority kind.** The dense search sliced results to `k` and filtered by kind afterwards, which asks "of the global top k, which are this kind" rather than "the top k of this kind". On the real store, asking for 3 memories returned 0 while asking for 200 returned 13 — same query, same corpus, so the shortfall was the slice. Distilled memories are 9.3% of artifacts but reached only 3.0% of injected hits (a 0.33x lift, i.e. actively disadvantaged), and any per-kind retrieval lane built to correct that would have silently returned nothing. Filtering now happens before truncation. Tests cover small k, multi-kind selection, ordering, and that the unfiltered path is untouched; they fail on the previous code.

- **Installing the proxy could silently disable memory entirely.** The hook skipped recall for any proxy-enabled agent, on the assumption that the proxy would serve it instead. That assumption is false for Claude Code authenticated with a subscription: `ANTHROPIC_BASE_URL` is honoured for API-key users, and a subscription session talks to Anthropic directly, so the proxy never sees the conversation. The hook stepped aside for a proxy that was not there. On the development machine both the recall and savings ledgers were silent for four days while Claude Code logged thousands of records a day, and nothing surfaced it. The skip is now conditional on the proxy having actually recorded a recall recently, and fails toward injecting: a duplicated recall costs some tokens, a skipped one costs the whole feature.

- **`memor install-compress-hook` crashed with `NameError: shutil`.** The command body called `shutil.which` and `cli.py` never imported it. The whole test suite passed, because the tests invoked the helper underneath the command and never the command itself. `tests/test_no_undefined_names.py` now runs pyflakes over the package, and `tests/test_cli_command_smoke.py` invokes every read-only command through typer's runner.
- **`memor service restart` never checked that the proxy came back.** Agents are configured to send their traffic to localhost, so a proxy that fails to start does not degrade to calling the provider directly — it takes every routed agent offline silently. `install()` now polls `/health` and either confirms the version that answered or prints an error naming `memor uninstall-proxy` as the way out.
- **The test suite wrote fixture rows into the developer's own savings ledger.** The `PostToolUse` tests spawn the hook as a real process and `main()` derives its database path from `Path.home()`, which a subprocess resolves for real no matter what the parent monkeypatched. Thirty-five rows accumulated in a live database and were then read back off the dashboard as genuine traffic. Subprocess tests now point `HOME` at a temporary directory, and an autouse fixture fails any test that grows the real ledger.
- **pytest's own output was not recognised as test output.** Progress lines (`tests/test_x.py .... [ 12%]`) matched no marker, so a 400-line run was classified as prose and passed through whole — the single most common shape of noisy tool output there is. Both the verbose and `-q` forms are now detected, but only when they make up at least 15% of the payload: a bare count-of-three rule reclassified an implementation report quoting four pytest lines among 145 lines of prose, and the crusher deleted 86 lines of explanation. Replaying old against new over 341 real files in this repo: zero classification changes.
- **The dashboard rendered large numbers in the machine's locale**, so 2,343,748 displayed as "23,43,748". Fixed at all eight call sites.
- **The net-of-cache figure was computed across mismatched populations**, subtracting cache overhead observed on a subset of requests from gross savings summed over all of them, which flattered compression. Low usage coverage is now detected and the figure labelled an upper bound.
- **`memor compression-worth` on an empty ledger told users to install the proxy**, with no mention of the hook path they may have just installed, and no hint that the likely cause was Claude Code not yet restarted.

### Changed
- **The cumulative savings curve now says when it last moved.** A running total cannot distinguish "saved nothing today" from "stopped recording weeks ago": both leave the same number on screen. On the development machine it read 2.2M for four days while the proxy was healthy and correctly configured, and the only way to tell which was happening was to query the database by hand. The panel now appends the last day that actually contributed once it is two or more days old, or says there was no proxied traffic in the window at all.

- **The recall sidecar served one prompt at a time.** `_handle_client` awaited `handle_request` directly on the asyncio event loop, but that call is fully blocking — SQLite scans, vector distance, token counting — so a second prompt could not even be accepted until the first finished. Measured through the real socket: one concurrent prompt 116 ms, sixteen 1,488 ms, rising linearly. Recalls now run in a small worker pool: sixteen concurrent prompts fall to 782 ms and thirty-two from 2,955 ms to 1,387 ms. The pool is deliberately four workers, because most of a recall runs under the GIL — a CPU-bound thread in the same interpreter took a 13 ms vector scan to 23 s, while identical load in separate processes left it at 20 ms.

- **The dashboard printed a rejected aggregate beside the words "no cost change".** The cost verdict returns `no_effect` when complexity bands disagree in direction, which on real data read -43%, +4%, -35% and +14% while the blended figure said -40.5%. The panel showed that -40.5% anyway, which is precisely the mix-shift that stratifying exists to stop anyone reading. It now explains that cost moved in different directions across task sizes and withholds the number. The sign was also inverted for the decisive verdicts: `cost_delta_pct` is a saving, so a positive value means the bill went down, and the panel rendered it the other way.

- **JSON compression now truncates long string values, not just oversized arrays.** Reviewing Headroom's approach against this corpus showed where memor's differs: it samples arrays but leaves values alone, and on real payloads 39% of the string mass sits in values over 200 characters — a pull request body, a review comment — inside objects with no oversized array anywhere. Those compressed by 0%. Keys, short values and structure are preserved so the agent can still navigate; diffs, patches and tracebacks are exempt because they are the payload rather than padding. JSON payloads went from 10.6% to 35.4% on a live corpus.
- **JSON compression could make a payload larger.** `json.dumps` defaults to `ensure_ascii=True`, re-encoding every non-ASCII character as a six-byte `\uXXXX` escape; a real payload with fourteen accented characters came back 42 tokens *bigger* than it went in. Output is now UTF-8 and the original is returned whenever the rewrite would not be shorter.

- **Recall's lexical channel no longer searches on words that match most of the corpus.** Profiling the real path put 167 ms of a 296 ms query inside `search_lexical`: terms are OR-combined, and `the` matched 91.4% of a 28,682-artifact store, so BM25 had to rank nearly every document to return eight. Terms above a 50% document-frequency ceiling are now dropped, with the ceiling chosen by sweep — every value from 90% down to 40% returned byte-identical top-8, 30% started changing results. Document frequencies and the corpus size are memoised, which is what makes it a win: uncached, the lookups cost as much as the ranking they saved (76.1 ms before, 74.8 ms with lookups, 60.4 ms memoised). End to end that is 21% off the retrieval path, with identical results on all 11 probe queries including one made entirely of stop words.

- **The README claimed recall was "Sub-15ms"; it is not.** Measured over 2,285 real recalls that returned hits on a live store: median 176 ms, 90th percentile 3.1 s. The ~2 ms figure for the embedding step is accurate and was never the problem — the time is retrieval and ranking over 26,734 active memories. The claim is now the measured distribution.
- **Recall latency is reported as percentiles rather than a mean.** One 135-second outlier dragged the average to 1,254 ms while the median recall took 176 ms, so the dashboard's headline number described nobody's actual wait. Misses are excluded: they return early and would flatter the figure for the wrong reason.

- **The dashboard leads with the rate on compressible payloads, not the blended one.** The blended figure is dominated by conversation history the compressor is designed never to touch, so it reads as compressor failure when what it measures is coverage. Both are shown, side by side, along with coverage itself. On a real ledger that is 7.3% versus 0.8%.
- **Net of cache renders in three distinguishable states** — unmeasured, a floor, or a measurement — because "the provider never told us" and "there were no cache writes" are different claims and only one of them is supportable.
- **The README leads with what was measured rather than what was built**, including that the hook fires on 21.2% of large Bash output with nearly all of the remainder held back by the source guard on purpose. Coverage is capped by safety, which is a property to state rather than a shortfall to hide. Every published figure must now name the population it was measured on, enforced by a test.
- `tokencount` imports tiktoken lazily. The hook runs on every Bash call, and paying ~50ms to conclude that `ls` produced two lines is a cost users pay hundreds of times a day for nothing. Declines now cost 19ms.

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
