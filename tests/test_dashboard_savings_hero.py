"""The cumulative savings panel, rendered from the shipped HTML.

A cumulative total cannot distinguish "saved nothing today" from "stopped
recording weeks ago" -- both leave the same number on screen. On this machine
it sat at 2.2M for four days while the proxy was healthy and correctly
configured, and the only way to tell was to query the database by hand.
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

_HARNESS = """
import fs from "node:fs";
const html = fs.readFileSync(process.argv[2], "utf8");
const body = html.match(/function renderSavingsHero\\(data\\)\\s*\\{([\\s\\S]*?)\\n  \\}\\n/)[1];
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const els = {};
["savings-pct","savings-before","savings-after","cum-saved-big",
 "cum-saved-lifetime","cum-saved-meta","content-types-list"]
  .forEach(id => els[id] = {id, textContent: ""});
const document = {getElementById: id => els[id] || null};
const fmt = n => Number(n).toLocaleString("en-US");
const esc = s => String(s);
const renderEquityChart = () => {};
new Function("document","fmt","esc","renderEquityChart","data", body + "\\n")(
  document, fmt, esc, renderEquityChart, payload);
const out = {};
for (const [id, e] of Object.entries(els)) out[id] = e.textContent;
console.log(JSON.stringify(out));
"""


def render(payload: dict, tmp_path: Path) -> dict[str, str]:
    harness = tmp_path / "hero.mjs"
    harness.write_text(_HARNESS)
    data = tmp_path / "payload.json"
    data.write_text(json.dumps(payload))
    proc = subprocess.run(["node", str(harness), str(INDEX), str(data)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _payload(day: str, saved: int, lifetime: int | None = None) -> dict:
    """A savings-ledger response. ``lifetime`` defaults to the window total.

    The endpoint gained a ``lifetime`` block when the hero stopped leading with
    the rolling window, and the headline reads from it. A fixture without one
    renders 0, so it is part of the shape now rather than optional.
    """
    total = saved if lifetime is None else lifetime
    return {
        "summary": {"tokens_before": 100, "tokens_after": 100 - saved,
                    "pct_saved": saved},
        "lifetime": {"tokens_saved": total, "hook_saved": total,
                     "proxy_saved": 0, "coverage_pct": 100.0},
        "per_day": [{"day": day, "tokens_before": 100,
                     "tokens_after": 100 - saved, "tokens_saved": saved,
                     "cumulative_saved": saved}],
        "content_types": [],
    }


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
    # The total itself is still shown; it is not wrong, only unexplained.
    assert out["cum-saved-big"] == "50"


def test_a_window_with_no_savings_says_so(tmp_path):
    out = render(_payload((date.today() - timedelta(days=3)).isoformat(), 0),
                 tmp_path)
    assert "no proxied traffic in this window" in out["cum-saved-meta"]


def test_yesterday_is_not_flagged_as_stale(tmp_path):
    """One quiet day is normal; flagging it would train users to ignore this."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    out = render(_payload(yesterday, 50), tmp_path)
    assert "last saved" not in out["cum-saved-meta"]


def test_headline_is_lifetime_even_when_the_window_is_quiet(tmp_path):
    """The reported symptom, as a test: a quiet 30d must not shrink the hero.

    A window holding 50 against a lifetime of 2,500,000 has to render the
    lifetime figure. Rendering the window is what made a quiet fortnight look
    like savings had collapsed from 2.5M to 256K.
    """
    out = render(_payload(date.today().isoformat(), 50, lifetime=2_500_000),
                 tmp_path)
    # The harness stubs fmt() as plain locale grouping, so this asserts the
    # value the hero chose, not the production abbreviation.
    assert out["cum-saved-big"] == "2,500,000"
    # The window is not discarded, it is demoted to context.
    assert "50 in the last 30d" in out["cum-saved-lifetime"]
