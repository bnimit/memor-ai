"""The compression panel's own JavaScript, executed against real payloads.

Every other dashboard test asserts on the JSON the API returns, which cannot
catch the failures that actually reach a user: a field the renderer reads but
the API never sends, an element id that does not exist in the markup, or a
number formatted so that 2,343,748 renders as 23,43,748. Those were all real,
and two of them were found by running this rather than by reading the code.

The renderer is extracted from the shipped HTML rather than duplicated, so
these tests fail when the real panel changes and not when a copy drifts.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parent.parent / "memor" / "dashboard" / "static" / "index.html"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to execute the panel"
)

#: Minimal DOM plus the globals the panel closes over. Only what the function
#: actually touches; anything more would be asserting on the shim.
_HARNESS = """
import fs from "node:fs";
const html = fs.readFileSync(process.argv[2], "utf8");
const body = html.match(/async function loadCompression\\(\\)\\s*\\{([\\s\\S]*?)\\n  \\}\\n/)[1];
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const els = {};
["cx-headline","cx-meta","cx-live","cx-cache","cx-hook","cx-types","cx-note"]
  .forEach(id => els[id] = {id, textContent:"", innerHTML:"", className:"", appendChild(){}});
const document = {
  getElementById: id => els[id] || null,
  createElement: () => ({className:"", innerHTML:"", appendChild(){}}),
};
const RW_LIVE_STYLE = {live:["pos","LIVE"], off:["rw-verdict-null","OFF"],
  pending:["rw-verdict-null","PENDING"], not_taking_effect:["neg","STALE"]};
const api = async () => payload;
const fn = new Function("document","RW_LIVE_STYLE","api",
  "return (async function loadCompression(){" + body + "\\n})();");
await fn(document, RW_LIVE_STYLE, api);
const out = {};
for (const [id, e] of Object.entries(els)) out[id] = e.innerHTML || e.textContent;
console.log(JSON.stringify(out));
"""


def render(payload: dict, tmp_path: Path) -> dict[str, str]:
    """Run the shipped renderer over a payload; return each element's text."""
    harness = tmp_path / "harness.mjs"
    harness.write_text(_HARNESS)
    data = tmp_path / "payload.json"
    data.write_text(json.dumps(payload))
    proc = subprocess.run(
        ["node", str(harness), str(INDEX), str(data)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    rendered = json.loads(proc.stdout)
    return {k: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", v)).strip()
            for k, v in rendered.items()}


def _payload(**overrides) -> dict:
    base = {
        "realized": {"requests": 5447, "passthrough_pct": 87.0,
                     "tokens_before": 284798930, "tokens_after": 282455182,
                     "saved": 2343748, "saved_pct": 0.8, "scored": True,
                     "by_type": {"log": 1188}},
        "compressible": {"tokens_before": 30202719, "tokens_after": 27858971,
                         "saved": 2343748, "saved_pct": 7.8, "coverage_pct": 13.0},
        "cache": None,
        "hook": None,
        "liveness": {"state": "live", "detail": "1,396 code payloads compressed"},
        "cost": None,
    }
    base.update(overrides)
    return base


def test_headline_leads_with_the_compressible_rate(tmp_path):
    """The blended rate understates the compressor by counting untouched history.

    Leading with 0.8% reads as "the compressor does nothing" when what it
    actually measures is that 87% of requests had nothing to compress.
    """
    out = render(_payload(), tmp_path)
    assert out["cx-headline"] == "7.8% saved"
    assert "on compressible payloads" in out["cx-meta"]
    # Coverage and the blended figure stay visible; neither is hidden.
    assert "13%" in out["cx-meta"]
    assert "0.8% blended" in out["cx-meta"]


def test_large_numbers_use_thousands_grouping(tmp_path):
    """A bare toLocaleString() follows the machine's locale.

    On this developer's machine that rendered 2,343,748 as "23,43,748", which
    is correct for the locale and wrong for every reader of the dashboard.
    """
    out = render(_payload(cache={
        "usage_requests": 1442, "reads": 165974863, "writes": 0,
        "writes_observed": False, "usage_coverage_pct": 26.5,
        "net_is_reliable": False, "hit_pct": 99.7, "overhead_units": 0,
        "net_saved_units": 2343748,
    }), tmp_path)
    assert "2,343,748" in out["cx-cache"]
    assert "23,43,748" not in out["cx-cache"]


def test_unmeasured_cache_is_not_shown_as_zero(tmp_path):
    out = render(_payload(cache=None), tmp_path)
    assert "unmeasured" in out["cx-cache"]


def test_unreported_writes_are_labelled_a_floor(tmp_path):
    out = render(_payload(cache={
        "usage_requests": 1442, "reads": 165974863, "writes": 0,
        "writes_observed": False, "usage_coverage_pct": 26.5,
        "net_is_reliable": False, "hit_pct": 99.7, "overhead_units": 0,
        "net_saved_units": 2343748,
    }), tmp_path)
    assert "floor rather than a measurement" in out["cx-cache"]


def test_partial_usage_coverage_is_labelled_an_upper_bound(tmp_path):
    out = render(_payload(cache={
        "usage_requests": 1442, "reads": 165974863, "writes": 500000,
        "writes_observed": True, "usage_coverage_pct": 26.5,
        "net_is_reliable": False, "hit_pct": 99.7, "overhead_units": 575000,
        "net_saved_units": 1768748,
    }), tmp_path)
    assert "upper bound" in out["cx-cache"]


def test_negative_net_is_stated_plainly(tmp_path):
    """The number the whole feature exists to be able to report."""
    out = render(_payload(cache={
        "usage_requests": 5000, "reads": 100, "writes": 900000,
        "writes_observed": True, "usage_coverage_pct": 97.0,
        "net_is_reliable": True, "hit_pct": 1.0, "overhead_units": 1035000,
        "net_saved_units": -40000,
    }), tmp_path)
    assert "-40,000" in out["cx-cache"]
    assert "not paying for itself" in out["cx-cache"]


def test_hook_savings_render_without_a_cache_caveat(tmp_path):
    out = render(_payload(hook={
        "requests": 33, "tokens_before": 153879, "tokens_after": 5478,
        "saved": 148401, "saved_pct": 96.4,
    }), tmp_path)
    assert "96.4% saved" in out["cx-hook"]
    assert "148,401" in out["cx-hook"]
    assert "no cache risk" in out["cx-hook"]


def test_missing_hook_path_prompts_installation(tmp_path):
    out = render(_payload(hook=None), tmp_path)
    assert "install-compress-hook" in out["cx-hook"]


def test_panel_survives_an_empty_ledger(tmp_path):
    """A fresh install must not render exceptions or NaN."""
    out = render(_payload(
        realized={"requests": 0, "passthrough_pct": 0, "tokens_before": 0,
                  "tokens_after": 0, "saved": 0, "saved_pct": 0,
                  "scored": False, "by_type": {}},
        compressible={"tokens_before": 0, "tokens_after": 0, "saved": 0,
                      "saved_pct": 0, "coverage_pct": 0},
        liveness={"state": "off", "detail": "disabled"},
    ), tmp_path)
    assert out["cx-headline"] == "not enough requests yet"
    for text in out.values():
        assert "NaN" not in text
        assert "undefined" not in text


def test_cheaper_verdict_renders_a_saving_as_negative(tmp_path):
    """cost_delta_pct is a saving: positive means the bill went down.

    The panel printed "−" whenever the value was >= 0, which is correct, and
    "+" otherwise -- but it applied that to a verdict line where the sign was
    already ambiguous, and paired it with an aggregate the verdict had
    rejected. Pinning the direction so a future edit cannot silently flip it.
    """
    out = render(_payload(cost={
        "verdict": "cheaper", "cost_delta_pct": 12.3,
        "n_before": 4021, "n_after": 386,
    }), tmp_path)
    assert "costs less" in out["cx-note"]
    assert "−12.3% per episode" in out["cx-note"]


def test_dearer_verdict_renders_an_increase_as_positive(tmp_path):
    out = render(_payload(cost={
        "verdict": "dearer", "cost_delta_pct": -8.0,
        "n_before": 4021, "n_after": 386,
    }), tmp_path)
    assert "costs MORE" in out["cx-note"]
    assert "+8.0% per episode" in out["cx-note"]


def test_no_effect_withholds_the_aggregate(tmp_path):
    """A decisive number beside "no change" is the mix-shift, rendered.

    no_effect is returned when complexity bands disagree in sign. On this
    machine's real data they read -43%, +4%, -35% and +14% while the blended
    figure said -40.5%, and the panel printed that -40.5% next to the words
    "no cost change". Stratifying exists precisely to stop anyone reading the
    blended number, so it is not shown.
    """
    out = render(_payload(cost={
        "verdict": "no_effect", "cost_delta_pct": -40.5,
        "n_before": 4021, "n_after": 386,
    }), tmp_path)
    assert "no measurable cost change" in out["cx-note"]
    assert "40.5" not in out["cx-note"], "the rejected aggregate must not appear"
    assert "different directions across task sizes" in out["cx-note"]


def test_insufficient_data_says_so(tmp_path):
    out = render(_payload(cost={
        "verdict": "insufficient_data", "cost_delta_pct": None,
        "n_before": 3, "n_after": 1,
    }), tmp_path)
    assert "not enough episodes" in out["cx-note"]
