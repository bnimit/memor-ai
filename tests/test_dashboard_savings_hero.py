"""The cumulative savings panel, rendered from the shipped HTML.

A cumulative total cannot distinguish "saved nothing today" from "stopped
recording weeks ago" -- both leave the same number on screen. On this machine
it sat at 2.2M for four days while the proxy was healthy and correctly
configured, and the only way to tell was to query the database by hand.

The 2026-10-02 overhaul keeps the lifetime absolute as the hero, but the
subtitle rate comes from /api/compression (30d realized), not lifetime %.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

INDEX = (Path(__file__).resolve().parent.parent
         / "memor" / "dashboard" / "static" / "index.html")

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to run the panel")

_HELPER_HARNESS = """
import fs from "node:fs";
const html = fs.readFileSync(process.argv[2], "utf8");
const m = html.match(/\\/\\* memor-dashboard-helpers \\*\\/([\\s\\S]*?)\\/\\* end-memor-dashboard-helpers \\*\\//);
if (!m) { console.error("helpers block missing"); process.exit(2); }
const expr = process.argv[3];
const out = new Function(m[1] + "; return (" + expr + ");")();
console.log(JSON.stringify(out));
"""

_HARNESS = """
import fs from "node:fs";
const html = fs.readFileSync(process.argv[2], "utf8");
const body = html.match(/function renderSavingsHero\\(data(?:, compression)?\\)\\s*\\{([\\s\\S]*?)\\n  \\}\\n/)[1];
const helpers = html.match(/\\/\\* memor-dashboard-helpers \\*\\/([\\s\\S]*?)\\/\\* end-memor-dashboard-helpers \\*\\//)[1];
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const compression = payload._compression || null;
const els = {};
["savings-pct","savings-before","savings-after","cum-saved-big",
 "cum-saved-lifetime","cum-saved-meta","content-types-list",
 "hero-subtitle","hero-dollar","hero-dollar-value","hero-rate-btn",
 "hero-footnote","hero-recall-pill"]
  .forEach(id => els[id] = {id, textContent: "", hidden: false});
els["hero-dollar"].hidden = true;
const document = {getElementById: id => els[id] || null};
const fmt = n => Number(n).toLocaleString("en-US");
const esc = s => String(s);
const renderEquityChart = () => {};
const localStorage = { _d: {}, getItem(k){ return this._d[k] ?? null; }, setItem(k,v){ this._d[k]=String(v); } };
new Function(
  "document","fmt","esc","renderEquityChart","localStorage","data","compression",
  helpers + "\\n" + body + "\\n"
)(document, fmt, esc, renderEquityChart, localStorage, payload, compression);
const out = {};
for (const [id, e] of Object.entries(els)) {
  out[id] = e.textContent;
  if (id === "hero-dollar") out["hero-dollar-hidden"] = !!e.hidden;
}
console.log(JSON.stringify(out));
"""


def run_helper(expr: str, tmp_path: Path):
    harness = tmp_path / "helper.mjs"
    harness.write_text(_HELPER_HARNESS)
    proc = subprocess.run(
        ["node", str(harness), str(INDEX), expr],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout)


def render(payload: dict, tmp_path: Path) -> dict[str, str]:
    harness = tmp_path / "hero.mjs"
    harness.write_text(_HARNESS)
    data = tmp_path / "payload.json"
    data.write_text(json.dumps(payload))
    proc = subprocess.run(["node", str(harness), str(INDEX), str(data)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout)


def _payload(day: str, saved: int, lifetime: int | None = None) -> dict:
    """A savings-ledger response. ``lifetime`` defaults to the window total."""
    total = saved if lifetime is None else lifetime
    before = 100 if lifetime is None else max(total, 100)
    after = before - (saved if lifetime is None else total)
    pct = round((1 - after / before) * 100, 1) if before else 0.0
    return {
        "summary": {"tokens_before": 100, "tokens_after": 100 - saved,
                    "pct_saved": saved},
        "lifetime": {
            "tokens_before": before,
            "tokens_after": after,
            "tokens_saved": total,
            "pct_saved": pct,
            "hook_saved": total,
            "proxy_saved": 0,
            "coverage_pct": 100.0,
        },
        "per_day": [{"day": day, "tokens_before": 100,
                     "tokens_after": 100 - saved, "tokens_saved": saved,
                     "cumulative_saved": saved}],
        "content_types": [],
        "_compression": {
            "realized": {
                "tokens_before": 701693,
                "tokens_after": 371797,
                "saved": 329896,
                "saved_pct": 47.0,
            },
            "cache": None,
        },
    }


def test_estimate_dollar_hides_when_zero(tmp_path):
    assert run_helper("estimateDollarSavings(0, 3)", tmp_path) is None


def test_estimate_dollar_math(tmp_path):
    assert run_helper("estimateDollarSavings(1000000, 3)", tmp_path) == 3.0


def test_recall_pill_saves_not_helps(tmp_path):
    assert run_helper("recallPillCopy('saves')", tmp_path)["kind"] == "saves"
    assert run_helper("recallPillCopy('helps')", tmp_path)["kind"] == "measuring"
    assert run_helper("recallPillCopy('no_effect')", tmp_path)["kind"] == "none"


def test_hook_dominated_threshold(tmp_path):
    assert run_helper(
        "isHookDominated({hook_saved:95, proxy_saved:4})", tmp_path) is True
    assert run_helper(
        "isHookDominated({hook_saved:80, proxy_saved:20})", tmp_path) is False


def test_recent_activity_shows_no_staleness_note(tmp_path):
    today = date.today().isoformat()
    out = render(_payload(today, 50), tmp_path)
    assert "last saved" not in out["cum-saved-meta"]
    assert "no proxied traffic" not in out["cum-saved-meta"]


def test_a_stale_curve_says_when_it_last_moved(tmp_path):
    """The failure this exists for: a number that looks current and is not."""
    stale = (date.today() - timedelta(days=4)).isoformat()
    out = render(_payload(stale, 50), tmp_path)
    assert "last saved" in out["cum-saved-meta"]
    assert stale in out["cum-saved-meta"]
    assert "4d ago" in out["cum-saved-meta"]
    assert out["cum-saved-big"] == "50"


def test_a_window_with_no_savings_says_so(tmp_path):
    out = render(_payload((date.today() - timedelta(days=3)).isoformat(), 0),
                 tmp_path)
    assert "no proxied traffic in this window" in out["cum-saved-meta"]


def test_yesterday_is_not_flagged_as_stale(tmp_path):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    out = render(_payload(yesterday, 50), tmp_path)
    assert "last saved" not in out["cum-saved-meta"]


def test_headline_is_lifetime_even_when_the_window_is_quiet(tmp_path):
    out = render(_payload(date.today().isoformat(), 50, lifetime=2_500_000),
                 tmp_path)
    assert out["cum-saved-big"] == "2,500,000"
    assert "50 in the last 30d" in out["cum-saved-lifetime"]


def test_subtitle_uses_compression_30d_not_lifetime_pct(tmp_path):
    """Hero subtitle must not reuse the lifetime blended % as 'recent'."""
    payload = _payload(date.today().isoformat(), 50, lifetime=2_500_000)
    payload["lifetime"] = {
        "tokens_before": 30_000_000, "tokens_after": 27_500_000,
        "tokens_saved": 2_500_000, "pct_saved": 8.3,
        "hook_saved": 250_000, "proxy_saved": 2_250_000, "coverage_pct": 14.0,
    }
    payload["_compression"] = {
        "realized": {
            "tokens_before": 246_800, "tokens_after": 21_100,
            "saved": 225_700, "saved_pct": 91.4,
        },
        "cache": None,
    }
    out = render(payload, tmp_path)
    assert out["cum-saved-big"] == "2,500,000"
    assert "91.4% last 30d" in out["hero-subtitle"]
    assert "8.3%" not in out["hero-subtitle"]
    assert out["hero-dollar-hidden"] is False
    assert "Hook-path" not in out["hero-footnote"]  # proxy share > 5%
    assert "Blended hook + proxy" in out["hero-footnote"]
    assert "API-equivalent" not in out["hero-footnote"]  # on the $ line, not footnote
    assert "flat subscription" in out["hero-footnote"]


def test_dollar_line_hidden_when_lifetime_zero(tmp_path):
    payload = _payload(date.today().isoformat(), 0, lifetime=0)
    # tokens_before must be truthy to enter the render path with zero saved
    payload["lifetime"]["tokens_before"] = 100
    payload["lifetime"]["tokens_after"] = 100
    payload["lifetime"]["tokens_saved"] = 0
    payload["lifetime"]["pct_saved"] = 0.0
    out = render(payload, tmp_path)
    assert out["hero-dollar-hidden"] is True
