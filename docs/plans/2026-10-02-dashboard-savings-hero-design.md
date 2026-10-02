# Dashboard savings-first overhaul — design

**Date:** 2026-10-02  
**Status:** Approved (brainstorm §§1–3); awaiting user review of this written spec  
**Approach:** Savings-first Overview (option 1)  
**Audience:** Builders + users — simple default Overview, evidence on Measure  
**Mockup:** session asset `memor-dashboard-overview-v3-mockup.jpg` (illustrative; not shipped in-repo)

## Problem

The dashboard shows too much (memories, recalls, projects, cross-tool, desks, charts) and underplays the only savings we can defend: **compression**. Lifetime vs 30d confusion made the hero feel broken (~7.7% lifetime blend vs ~47% recent realized). Recall KPIs imply value we have **not** proven (matched ATT no_effect; prove-recall still needs G2).

## Evidence we may show

| Surface | Proven? | Source | Notes |
|---|---|---|---|
| Compression tokens saved | Yes (realized) | Savings ledger + `/api/compression` | Hook path = tokenizer estimate; provider never saw uncompressed payload |
| Compression % (30d) | Yes | `/api/compression` realized | Separate from lifetime absolute; do not apply one % to the other count |
| Net-of-cache | Sometimes | `/api/compression.cache` | `null` = unmeasured (not zero) |
| Dollar savings | Estimate only | Client: tokens × rate | API-equivalent; not invoice |
| Recall reduces work | **No** (as of 2026-10-02) | `/api/recall-worth` | Pill status only; Measure keeps observational caveat until prove-recall G2 |

## Goals

1. Overview first viewport = one composition: “are you saving tokens?”
2. Hero = lifetime tokens saved + 30d realized % + optional ≈$ estimate.
3. Recall demoted to a status pill (not a savings KPI).
4. Measure holds compression receipt, ledger, and recall evidence for builders.
5. Honesty copy stays visible; never claim invoice-proven hook savings or recall ROI early.

## Non-goals

- Provider invoice sync / billed reconciliation API.
- Agent-desk KPI trim (deferred; out of this PR unless it duplicates the Overview hero).
- Provenance graph visual redesign.
- Light-mode theme toggle.
- Marketing claims beyond what meters support.
- Changing compression or recall runtime behavior.

## Information architecture

| Tab | Job |
|---|---|
| **Overview** (default) | Hero savings receipt + status + sparkline + recall pill |
| **Measure** | Compression evidence, lifetime periods, recall ATT / baseline, secondary ops |
| **Agent desks** | Per-agent ops (role unchanged this PR) |
| **Provenance** | Memory lineage (unchanged) |

### Overview — first viewport only

1. **Primary:** lifetime `tokens_saved` from `/api/savings-ledger` → `lifetime.tokens_saved` (endpoint also returns a windowed `summary` / `per_day`; do not display window summary as the hero absolute).
2. **Subtitle:** last 30 days realized % and before→after from `/api/compression` → `realized` (parallel fetch). Not a % derived from lifetime.
3. **Secondary:** `≈ $X at $Y / 1M · API-equivalent estimate` with editable rate (`localStorage`).
4. **Honesty footnote (dynamic):**
   - **Hook-dominated** when lifetime (or 30d period totals) proxy `tokens_before` is &lt; 5% of hook+proxy `tokens_before`: show hook-path tokenizer clause only.
   - Else: “Blended hook + proxy ledger” clause.
   - If `/api/compression.cache` is null: “net-of-cache unmeasured.”
   - Always when $ shown: “On a flat subscription this is headroom, not a lower bill.”
5. **Status strip:** proxy / hook / daemon LEDs (existing).
6. **Recall pill:** status only; click/focus switches to Measure. Never hit counts as savings.
7. **Sparkline:** `/api/savings-ledger?days=30` → `per_day[].cumulative_saved` (window equity under the lifetime hero — **not** a full-history series; ledger API caps `days` at 90). One chart only above the fold.

**Removed from Overview:** Memories / Recalls / Projects KPI quartet, cross-tool collage, desk health walls, competing charts.

### Recall pill state machine

| Source verdict / condition | Pill copy |
|---|---|
| `insufficient_data` / error | `Recall · measuring` |
| `no_effect` or `costs` | `Recall · no measurable savings yet` |
| `saves` from `/api/recall-worth` (matched ATT cleared MDE; API token is `saves`, not `helps`) | `Recall · effect detected` → Measure |
| Default / loading | `Recall · measuring` |

Pill gating uses the live recall-worth verdict only. Prove-recall G2 remaining open does **not** block the pill, but Measure copy must still say observational upper-bound / not marketing-ready until G2 passes.

Do not upgrade the pill from activity volume (recalls, hit rate).

### Measure — order

1. **Compression receipt** — `/api/compression`: 30d realized, hook block, cache row, by-type, liveness.
2. **Lifetime ledger** — periods / cumulative; hook vs proxy **separate** (`/api/savings-periods` note).
3. **Recall evidence** — `/api/recall-worth` + `/api/recall-baseline` if stamped. Prove-recall G2 status: show only if a **runtime-readable stamp** exists (e.g. baseline / config); do **not** require shipping `docs/eval/*.json` to end-user installs. No “tokens saved by recall.”
4. **Secondary (below fold):** recall activity trend, efficiency, proxy-by-agent, cross-tool, memory counts.

Do **not** use `/api/roi` / old recall-injection “reduction_pct” as compression savings.

## Dollar estimate rules

- Formula: `lifetime_tokens_saved × (rate_per_M / 1e6)`.
- Default rate: **$3 / 1M** tokens; persist in `localStorage` key `memor.dashboard.tokenRatePerM` (number).
- Edit UX: click the rate → inline `<input type="number">` (min 0, step 0.1); blur/Enter commits; Escape cancels. No `prompt()`.
- Invalid / empty input → revert to last good rate (or default $3).
- Label always includes **API-equivalent estimate**.
- If lifetime saved = 0 → **hide** the $ line (do not show `$0.00`).
- Single rate only this PR (no separate cache-write tier).

## Empty / zero states

| Condition | Overview | Measure |
|---|---|---|
| No ledger rows | “No savings measured yet” + install/hook CTA | Compression panel explains paths |
| Lifetime > 0, 30d = 0 | Show lifetime; subtitle `0% last 30d` | Liveness / coverage callout |
| Recall `insufficient_data` | Pill: measuring | Need-more-episodes copy |
| Daemon/hooks down | Red LEDs; existing banner | Same |

Zero-traffic (e.g. agents without hook compression) must not look like a product outage: explain missing compressible path.

## Data contract

| UI element | API / field | Must not |
|---|---|---|
| Hero absolute | `/api/savings-ledger` → `lifetime.tokens_saved` | Use window `summary.tokens_saved` as hero |
| Hero subtitle % | `/api/compression` → `realized.saved_pct` + before/after | Use lifetime blended % as “recent” |
| Hook/proxy footnote | Period totals from `/api/savings-periods` or ledger path split if available | Guess from a single request |
| Hook detail (Measure) | `compression.hook` or null | Render zeroes when null |
| Cache | `compression.cache` or null | Treat null as $0 saved |
| Recall pill | `/api/recall-worth.overall.verdict` (`saves` \| `costs` \| `no_effect` \| `insufficient_data`) | Use recall_log counts; invent `helps` |
| Sparkline | `/api/savings-ledger?days=30` → `per_day` | Claim full-lifetime equity; second above-fold chart |

Overview polls: savings-ledger + compression + recall-worth (+ existing health). No new aggregate endpoint required for v1.

## Visual language (§3)

**Direction:** calm receipt fintech — dark charcoal, warm paper tint behind the hero number only, green for saved, amber for measuring/caution. No purple glow, no KPI card wall, no emoji.

**CSS variables (indicative):** `--bg`, `--surface`, `--text`, `--muted`, `--saved`/`--ok`, `--warn`, `--hero-paper`.

**Typography:** expressive mono for the hero number; one dominant figure; quiet section labels.

**Motion (respect `prefers-reduced-motion`):**
1. Hero number count-up once on load.
2. Sparkline draw left→right on first paint.
3. Status LED pulse only when unhealthy.

**Scoped to:** Overview + Measure chrome. Not Provenance graph; no light-mode toggle this pass.

**Responsive:** Hero stacks cleanly on narrow viewports (number wraps/scales; status chips wrap; sparkline full width). No separate mobile redesign.

## Implementation sketch (non-binding; plan follows approval)

Primary touch: `memor/dashboard/static/index.html` (Overview markup/CSS/JS). Prefer existing `/api/savings-ledger`, `/api/compression`, `/api/savings-periods`, `/api/recall-worth` — avoid new endpoints unless a thin aggregate is clearly cheaper than multiple polls.

## Testing

- Manual: Overview with real ledger data; with empty ledger; with 30d zero / lifetime > 0.
- Manual: edit rate → $ line updates; reload persists rate; saved=0 hides $; Escape cancels edit.
- Manual: recall pill maps `no_effect`, `insufficient_data`, and `saves` / `costs` if fixtureable.
- Manual: footnote switches hook-only vs blended when proxy share crosses 5%.
- If UI unit tests exist for dashboard helpers, cover rate math and pill mapping; do not add a browser suite in this PR.

## Spec self-review (2026-10-02)

Fixed before plan:

1. **Verdict token** — API returns `saves`, not `helps`.
2. **Named endpoints** — hero/sparkline from `/api/savings-ledger`; subtitle from `/api/compression`; sparkline is window equity (≤90d API cap), not full lifetime series.
3. **Hook-dominated rule** — &lt;5% proxy share of tokens_before.
4. **Rate edit UX** — inline number input; no `prompt()`.
5. **Prove-recall on Measure** — no dependency on shipping `docs/eval` to installs.
6. **Evidence vs pill** — aligned: G2 gates marketing copy on Measure, not the pill.

## Success criteria

- First viewport answers “tokens saved?” without competing KPIs.
- Compression caveats visible without opening a docs page.
- Recall never presented as proven dollar/token savings until the state machine allows it.
- Builder can still audit evidence on Measure in one scroll.
