# Dashboard Savings-First Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Overview into a savings-first receipt (lifetime tokens + 30d % + ≈$ estimate + recall status pill) and keep evidence on Measure, per `docs/plans/2026-10-02-dashboard-savings-hero-design.md`.

**Architecture:** Keep logic in `memor/dashboard/static/index.html` (existing pattern). Extract pure helpers as named functions inside the same script so Node harness tests can eval them. Wire Overview to `/api/savings-ledger`, `/api/compression`, `/api/recall-worth`. No new backend endpoints.

**Tech Stack:** Static HTML/CSS/JS dashboard, FastAPI JSON APIs (existing), pytest + Node harness tests.

## Global Constraints

- Hero absolute = `/api/savings-ledger` → `lifetime.tokens_saved` only.
- Subtitle % = `/api/compression` → `realized` (30d), never lifetime %.
- $ = API-equivalent estimate; default `$3/1M`; `localStorage` key `memor.dashboard.tokenRatePerM`.
- Recall pill uses verdict `saves`|`costs`|`no_effect`|`insufficient_data` — never hit counts.
- Hook-dominated footnote: `proxy_saved / (hook_saved + proxy_saved) < 0.05` from lifetime (saved-share; ledger has no path `tokens_before` split).
- Do not use `/api/roi` as compression savings.
- Agent-desk KPI trim, Provenance redesign, light mode: out of scope.
- Preserve `prefers-reduced-motion`.

## File map

| File | Role |
|---|---|
| `memor/dashboard/static/index.html` | Overview markup/CSS, hero render, pill, rate edit, Measure order, loaders |
| `tests/test_dashboard_savings_hero.py` | Update/extend Node harness for new hero DOM + helpers |
| `tests/test_dashboard_agent_desk.py` | Only if HTML id assertions for Overview KPIs break |

---

### Task 1: Pure helpers + unit harness

**Files:**
- Modify: `memor/dashboard/static/index.html` (add helpers near other utils)
- Modify: `tests/test_dashboard_savings_hero.py`

**Interfaces:**
- Produces:
  - `estimateDollarSavings(tokensSaved, ratePerM) → number|null` (null if tokensSaved ≤ 0)
  - `recallPillCopy(verdict) → {text, kind}` where kind is `measuring`|`none`|`saves`|`costs`
  - `isHookDominated(life) → boolean` using hook_saved/proxy_saved
  - `DEFAULT_TOKEN_RATE_PER_M = 3`
  - `TOKEN_RATE_STORAGE_KEY = 'memor.dashboard.tokenRatePerM'`

- [ ] **Step 1: Write failing helper tests** in `tests/test_dashboard_savings_hero.py` (Node harness that extracts helper function bodies by name, or a small embedded script block tagged `/* memor-dashboard-helpers */` … `/* end-memor-dashboard-helpers */`).

```python
def test_estimate_dollar_hides_when_zero():
    assert run_helper("estimateDollarSavings(0, 3)") is None

def test_estimate_dollar_math():
    # 1_000_000 tokens at $3/M → 3.0
    assert run_helper("estimateDollarSavings(1000000, 3)") == 3.0

def test_recall_pill_saves_not_helps():
    assert run_helper("recallPillCopy('saves')")["kind"] == "saves"
    assert run_helper("recallPillCopy('helps')")["kind"] == "measuring"  # unknown → measuring

def test_hook_dominated_threshold():
    assert run_helper("isHookDominated({hook_saved:95, proxy_saved:4})") is True
    assert run_helper("isHookDominated({hook_saved:80, proxy_saved:20})") is False
```

- [ ] **Step 2: Run tests — expect FAIL** (helpers missing)

- [ ] **Step 3: Implement helpers** in `index.html` inside the tagged block

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit** `test: add dashboard savings helper coverage`

---

### Task 2: Overview markup + visual tokens

**Files:**
- Modify: `memor/dashboard/static/index.html` (CSS `:root`, Overview pane HTML)

**Interfaces:**
- DOM ids (stable for Task 3 + tests):
  - `hero-tokens-saved` (big number; keep alias sync to `cum-saved-big` OR rename and update all tests to `hero-tokens-saved`)
  - `hero-subtitle` (30d % · before → after)
  - `hero-dollar` / `hero-rate` (rate display + edit)
  - `hero-footnote`
  - `hero-recall-pill`
  - `savings-equity` (sparkline)
  - `cum-saved-meta` (staleness; keep)
  - Remove/hide Portfolio KPI grid, cross-tool, desks, projects, recent recalls from Overview (move projects/cross-tool sections into Measure below-fold if not already there)

**Decision:** Keep id `cum-saved-big` as the hero number to minimize harness churn; add the new surrounding structure around it.

- [ ] **Step 1:** Add CSS variables `--saved`, `--hero-paper`; hero layout styles (warm radial behind number); recall pill; rate input.

- [ ] **Step 2:** Replace Overview Portfolio section with hero receipt structure matching the mockup; leave status-row; one sparkline section; remove competing Overview sections (move markup to Measure if missing).

- [ ] **Step 3:** Smoke: open HTML / run existing tests that only assert ids exist — update `test_dashboard_agent_desk` Overview assumptions if needed.

- [ ] **Step 4: Commit** `ui: savings-first Overview shell`

---

### Task 3: Wire renderSavingsHero + compression subtitle + $ + footnote

**Files:**
- Modify: `renderSavingsHero` and loaders in `index.html`
- Modify: `tests/test_dashboard_savings_hero.py`

**Interfaces:**
- `renderSavingsHero(ledger, compression)` — compression may be null while loading
- `loadSavingsHero` fetches ledger + compression in parallel
- Rate: read/write `localStorage`; inline edit on `#hero-rate`

- [ ] **Step 1: Update harness** for new fields: `hero-subtitle`, `hero-dollar`, `hero-footnote`; keep lifetime absolute assertions; **change** KPI test: subtitle % comes from compression fixture, not `lifetime.pct_saved`.

- [ ] **Step 2: Run — FAIL** on new expectations

- [ ] **Step 3: Implement** `renderSavingsHero`:
  - Empty: no `lifetime.tokens_before` → “No savings measured yet”; hide $
  - Big number = `lifetime.tokens_saved`
  - Subtitle from `compression.realized` when present; else “—”
  - $ via `estimateDollarSavings`
  - Footnote via `isHookDominated` + cache null + flat-plan clause when $ visible
  - Keep staleness logic on `cum-saved-meta`
  - Count-up animation optional; skip if `prefers-reduced-motion`

- [ ] **Step 4: Wire rate edit** listeners once at init

- [ ] **Step 5: Tests PASS**

- [ ] **Step 6: Commit** `feat: wire Overview compression subtitle and dollar estimate`

---

### Task 4: Recall pill + Overview refresh set

**Files:**
- Modify: `index.html` (`renderRecallPill`, `refreshOverview`)

- [ ] **Step 1: Test** pill copy mapping via helper tests (already Task 1); add DOM test that `renderRecallPill('no_effect')` sets pill text.

- [ ] **Step 2: Implement** `renderRecallPill`; click → `showPane('measure')` (use existing pane switcher).

- [ ] **Step 3: `refreshOverview`** = health + proxy status + savings hero (ledger+compression) + recall worth for pill only. Stop loading summary/projects/recalls/cross-tool on Overview (those move to Measure refresh).

- [ ] **Step 4: Commit** `feat: Overview recall status pill`

---

### Task 5: Measure reorder + secondary panels

**Files:**
- Modify: Measure pane HTML order in `index.html`
- Modify: `refreshMeasure` to include moved loaders (summary/projects/cross-tool if moved)

- [ ] **Step 1:** Reorder Measure sections: compression → savings periods → recall worth → baseline → secondary (trend, efficiency, proxy-by-agent, cross-tool, memory/projects if moved).

- [ ] **Step 2:** Ensure compression panel still works (`tests/test_dashboard_compression_panel.py`).

- [ ] **Step 3: Commit** `ui: Measure pane evidence order`

---

### Task 6: Visual polish + motion + verify

**Files:**
- Modify: `index.html` CSS/JS

- [ ] **Step 1:** Hero paper glow, mono hero number scale, amber pill border, responsive stack.

- [ ] **Step 2:** Count-up + sparkline draw gated on `prefers-reduced-motion`.

- [ ] **Step 3:** Run full dashboard-related pytest subset:

```bash
.venv/bin/pytest tests/test_dashboard_savings_hero.py tests/test_dashboard_compression_panel.py tests/test_dashboard_agent_desk.py tests/test_dashboard_idle_banner.py -q
```

- [ ] **Step 4: Commit** `ui: polish savings-first dashboard motion and theme`

---

## Spec coverage checklist

| Spec item | Task |
|---|---|
| Lifetime hero + 30d subtitle | 3 |
| ≈$ estimate + rate edit | 1, 3 |
| Dynamic honesty footnote | 3 |
| Recall pill state machine | 1, 4 |
| Remove Overview KPI wall | 2, 4 |
| Measure order | 5 |
| Visual §3 | 2, 6 |
| Empty states | 3 |
| No `/api/roi` as savings | 4 (Overview drop) |
| Hook-dominated 5% | 1, 3 |

## Execution

Prefer **inline execution** in this session (user asked to build out).
