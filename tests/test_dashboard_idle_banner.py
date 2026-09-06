"""The idle-path banner, rendered from the shipped HTML.

memor's launchd services were not running between 22 Aug and 6 Sep 2026. Claude
ran daily throughout -- 4,479 recalls are logged across that window -- and not
one compressed request was recorded. The only symptom anywhere in the product
was a savings curve that stopped moving, which is exactly what a genuinely quiet
fortnight looks like. Nothing said which had happened.

The banner exists to make those two states distinguishable. Its *decision* is
the whole feature, so it is exercised here against the real shipped markup
rather than asserted from the endpoint alone: the endpoint returning a correct
idle age is worth nothing if the page fails to act on it.

The case that matters most is recovery. A warning that fires correctly but
never clears trains the user to ignore it, which would leave them worse off
than the silence this replaced.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX = (Path(__file__).resolve().parent.parent
         / "memor" / "dashboard" / "static" / "index.html")

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to run the panel")

#: Extracts the banner decision path verbatim from the shipped page and runs it
#: against a stub DOM. Copying the logic into the test would prove only that the
#: copy works.
_HARNESS = """
import fs from "node:fs";
const html = fs.readFileSync(process.argv[2], "utf8");
const warn = html.match(/var PATH_IDLE_WARN_DAYS = \\d+;/)[0];
const idleFn = html.match(/function idlePathWarning\\(h\\) \\{[\\s\\S]*?\\n  \\}/)[0];
const healthFn = html.match(/function renderHealth\\(h\\) \\{[\\s\\S]*?\\n  \\}\\n/)[0];
const els = {banner:{style:{display:"none"}}, "banner-msg":{innerHTML:""}};
const document = {getElementById: id => els[id] || null};
const esc = s => String(s);
const renderHealth = new Function(
  "document","esc", warn+"\\n"+idleFn+"\\n"+healthFn+"\\nreturn renderHealth;"
)(document, esc);
renderHealth(JSON.parse(process.argv[3]));
console.log(JSON.stringify({
  display: els.banner.style.display,
  msg: els["banner-msg"].innerHTML,
}));
"""


def render(health: dict, tmp_path) -> dict:
    script = tmp_path / "h.mjs"
    script.write_text(_HARNESS)
    out = subprocess.run(
        ["node", str(script), str(INDEX), json.dumps(health)],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _health(hook_days, proxy_days, status="full") -> dict:
    return {
        "onboarding_status": status,
        "compression_paths": {
            "hook": {"idle_days": hook_days},
            "proxy": {"idle_days": proxy_days},
        },
    }


def test_the_fortnight_of_silence_is_named(tmp_path):
    """The exact state this machine was in, and said nothing about."""
    out = render(_health(15.1, 15.1), tmp_path)
    assert out["display"] == "flex"
    assert "hook (15.1d)" in out["msg"]
    assert "proxy (15.1d)" in out["msg"]


def test_the_warning_clears_once_traffic_resumes(tmp_path):
    """The acceptance path: restart the services and the banner must go.

    A warning that fires correctly and never clears is worse than no warning,
    because it teaches the user to ignore the banner that also carries the
    onboarding errors.
    """
    assert render(_health(15.1, 15.1), tmp_path)["display"] == "flex"
    assert render(_health(0.0, 0.0), tmp_path)["display"] == "none"


def test_one_dead_path_is_named_alone(tmp_path):
    """Hook and proxy fail independently; naming both would misdirect."""
    out = render(_health(0.2, 9.9), tmp_path)
    assert "proxy (9.9d)" in out["msg"]
    assert "hook" not in out["msg"]


def test_a_quiet_weekend_does_not_warn(tmp_path):
    """Below the threshold is ordinary light use, not a fault."""
    assert render(_health(1.9, 1.9), tmp_path)["display"] == "none"


def test_a_fresh_install_does_not_warn(tmp_path):
    """Never-configured is a setup state the onboarding banner already owns.

    Firing here would put the warning on every new machine, which is the
    fastest way to make it invisible.
    """
    assert render(_health(None, None), tmp_path)["display"] == "none"


def test_onboarding_problems_still_take_precedence(tmp_path):
    """An unconfigured install must not be told its proxy went quiet."""
    out = render(_health(99, 99, status="no_artifacts"), tmp_path)
    assert "No artifacts ingested" in out["msg"]
    assert "compression" not in out["msg"]


def test_a_page_talking_to_an_older_server_does_not_crash(tmp_path):
    """compression_paths is new; a cached page may not receive it.

    The dashboard is a long-lived tab, so a page predating the field is a real
    state rather than a hypothetical one.
    """
    out = render({"onboarding_status": "full"}, tmp_path)
    assert out["display"] == "none"
