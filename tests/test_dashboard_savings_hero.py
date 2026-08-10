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
 "cum-saved-meta","content-types-list"].forEach(id => els[id] = {id, textContent: ""});
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


def _payload(day: str, saved: int) -> dict:
    return {
        "summary": {"tokens_before": 100, "tokens_after": 100 - saved,
                    "pct_saved": saved},
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
    assert "50 saved" in out["cum-saved-big"]


def test_a_window_with_no_savings_says_so(tmp_path):
    out = render(_payload((date.today() - timedelta(days=3)).isoformat(), 0),
                 tmp_path)
    assert "no proxied traffic in this window" in out["cum-saved-meta"]


def test_yesterday_is_not_flagged_as_stale(tmp_path):
    """One quiet day is normal; flagging it would train users to ignore this."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    out = render(_payload(yesterday, 50), tmp_path)
    assert "last saved" not in out["cum-saved-meta"]
