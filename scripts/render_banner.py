#!/usr/bin/env python3
"""Generate the README banner.

Written as a script rather than a checked-in binary so the banner can be
regenerated when the wordmark or palette changes, and so the design is
reviewable as text. Colours are taken from the dashboard's own CSS variables,
so the banner and the product look like one thing.

Usage:
    python scripts/render_banner.py docs/images/banner.svg
"""
from __future__ import annotations

import sys

# Straight from memor/dashboard/static/index.html :root
BG = "#0c0e12"
SURFACE = "#161a22"
BORDER = "#2a3140"
TEXT = "#e8e6e1"
TEXT_SECONDARY = "#a8a29a"
TEXT_MUTED = "#6d655c"
ACCENT = "#e89320"
OK = "#2fd67b"

W, H = 1280, 320

# The agents, drawn as nodes feeding one store. The point of the picture is the
# direction of the arrows: many tools in, one memory out, which is the whole
# product in a glance.
AGENTS = ["Claude Code", "Cursor", "Codex", "Copilot", "Kimi", "Goose"]


def banner() -> str:
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="memor: one local memory shared by every AI coding agent">',
        "<defs>",
        #  Subtle warmth behind the wordmark, not a gradient wash over everything.
        f'<radialGradient id="glow" cx="50%" cy="42%" r="55%">'
        f'<stop offset="0%" stop-color="{ACCENT}" stop-opacity="0.10"/>'
        f'<stop offset="100%" stop-color="{ACCENT}" stop-opacity="0"/>'
        f"</radialGradient>",
        f'<linearGradient id="rule" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{ACCENT}" stop-opacity="0"/>'
        f'<stop offset="50%" stop-color="{ACCENT}" stop-opacity="0.55"/>'
        f'<stop offset="100%" stop-color="{ACCENT}" stop-opacity="0"/>'
        f"</linearGradient>",
        "</defs>",
        f'<rect width="{W}" height="{H}" fill="{BG}"/>',
        f'<rect width="{W}" height="{H}" fill="url(#glow)"/>',
    ]

    # Agent row: six labelled pills across the top.
    pill_y = 58
    gap = 14
    widths = [max(88, len(a) * 8 + 30) for a in AGENTS]
    total = sum(widths) + gap * (len(AGENTS) - 1)
    x = (W - total) / 2
    centres: list[float] = []
    for label, pw in zip(AGENTS, widths):
        centres.append(x + pw / 2)
        parts.append(
            f'<rect x="{x:.0f}" y="{pill_y}" width="{pw}" height="30" rx="15" '
            f'fill="{SURFACE}" stroke="{BORDER}"/>'
        )
        parts.append(
            f'<text x="{x + pw / 2:.0f}" y="{pill_y + 20}" text-anchor="middle" '
            f'font-family="ui-sans-serif,-apple-system,Segoe UI,sans-serif" '
            f'font-size="13" fill="{TEXT_SECONDARY}">{label}</text>'
        )
        x += pw + gap

    # Arrows converging on the wordmark: the shared store.
    for cx in centres:
        parts.append(
            f'<path d="M {cx:.0f} {pill_y + 32} C {cx:.0f} {pill_y + 58}, '
            f'{W / 2:.0f} {pill_y + 52}, {W / 2:.0f} {pill_y + 74}" '
            f'fill="none" stroke="{BORDER}" stroke-width="1.2"/>'
        )

    # Wordmark. The trailing dot is the dashboard's own mark, drawn as a circle
    # rather than a period: at this size a typographic full stop renders as a
    # square block in most sans stacks, which reads as a glitch.
    parts.append(
        f'<text x="{W / 2 - 14}" y="205" text-anchor="middle" '
        f'font-family="ui-sans-serif,-apple-system,Segoe UI,sans-serif" '
        f'font-size="76" font-weight="600" letter-spacing="-2" fill="{TEXT}">'
        f'memor</text>'
    )
    parts.append(f'<circle cx="{W / 2 + 122}" cy="198" r="8" fill="{ACCENT}"/>')

    parts.append(
        f'<rect x="{W / 2 - 220:.0f}" y="228" width="440" height="1" fill="url(#rule)"/>'
    )

    parts.append(
        f'<text x="{W / 2}" y="262" text-anchor="middle" '
        f'font-family="ui-sans-serif,-apple-system,Segoe UI,sans-serif" '
        f'font-size="19" fill="{TEXT_SECONDARY}">'
        f"One local memory shared by every AI coding agent</text>"
    )

    # Three properties, stated as facts rather than adjectives. Widths are
    # measured from the rendered text so the row centres properly instead of
    # drifting left as labels change.
    facts = [("Local first", OK), ("No API key", OK), ("No plugin to install", OK)]
    CHAR = 7.8
    GAP = 44
    row_w = sum(len(l) * CHAR + 12 for l, _ in facts) + GAP * (len(facts) - 1)
    fx = W / 2 - row_w / 2
    for label, colour in facts:
        parts.append(f'<circle cx="{fx:.0f}" cy="291" r="3.5" fill="{colour}"/>')
        parts.append(
            f'<text x="{fx + 12:.0f}" y="296" '
            f'font-family="ui-monospace,SFMono-Regular,Menlo,monospace" '
            f'font-size="13" fill="{TEXT_MUTED}">{label}</text>'
        )
        fx += len(label) * CHAR + 12 + GAP

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    dest = sys.argv[1] if len(sys.argv) > 1 else "docs/images/banner.svg"
    with open(dest, "w") as fh:
        fh.write(banner())
    print(f"wrote {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
