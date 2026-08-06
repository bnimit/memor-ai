"""jcode must appear in the dashboard the way kimi and goose do.

An agent that ingests but never shows up in the UI is invisible work: the
per-agent breakdown, the recall table badges and the colour legend all key off
these lists, so missing one silently drops jcode from that view.
"""
from __future__ import annotations

from pathlib import Path

import memor.hook_server as hook_server

INDEX = Path(__file__).resolve().parents[1] / "memor" / "dashboard" / "static" / "index.html"


def _html() -> str:
    return INDEX.read_text()


def test_jcode_is_a_core_agent():
    """CORE_AGENTS drives the per-agent breakdown and filter chips."""
    html = _html()
    line = next(l for l in html.splitlines() if "var CORE_AGENTS" in l)
    assert "jcode" in line, "jcode missing from CORE_AGENTS"
    # The agents it should sit alongside are still there.
    for peer in ("kimi", "goose", "claude"):
        assert peer in line


def test_jcode_has_a_colour_and_a_badge():
    html = _html()
    assert "--jcode:" in html, "no colour variable for jcode"
    assert ".badge-jcode" in html, "no badge style for jcode"
    assert "jcode: 'var(--jcode)'" in html, "jcode missing from AGENT_COLORS"


def test_jcode_has_a_display_label():
    html = _html()
    assert "jcode: 'Jcode'" in html
    assert "jcode: ['badge-jcode', 'Jcode']" in html


def test_every_core_agent_has_a_colour():
    """A new agent must not be added to one list and forgotten in the other."""
    html = _html()
    line = next(l for l in html.splitlines() if "var CORE_AGENTS" in l)
    agents = [a.strip().strip("'\"") for a in
              line.split("[", 1)[1].split("]", 1)[0].split(",")]
    for agent in agents:
        assert f"{agent}:" in html, f"{agent} has no entry in AGENT_COLORS"


def test_hook_server_accepts_a_jcode_stamp():
    """detect_agent gates on an allow-list; jcode has to be on it."""
    assert hook_server.detect_agent({"_memor_agent": "jcode"}) == "jcode"
