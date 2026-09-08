#!/usr/bin/env bash
# Regenerate every image the README embeds, from live output.
#
# README images rot silently: the product changes, the screenshot does not, and
# nobody notices until a user points at a figure that no longer exists. This
# script rebuilds all of them from the running dashboard and the real CLI, so
# refreshing them is one command rather than a manual ritual nobody repeats.
#
# Usage:  scripts/refresh-readme-images.sh
# Needs:  the dashboard running (memor service install, or memor dashboard)

set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
PORT="${MEMOR_DASHBOARD_PORT:-8420}"
OUT=docs/images
mkdir -p "$OUT"

if ! curl -sf --max-time 3 "http://localhost:$PORT/api/summary" >/dev/null; then
  echo "dashboard not reachable on :$PORT — start it with 'memor dashboard'" >&2
  exit 1
fi

echo "capturing dashboard..."
"$PY" - "$PORT" <<'PYEOF'
import sys
from playwright.sync_api import sync_playwright

port = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    pg.goto(f"http://localhost:{port}/", wait_until="networkidle")
    pg.wait_for_timeout(3500)
    # The onboarding banner is a live warning about this machine, not a feature.
    pg.evaluate("var b=document.getElementById('banner'); if(b) b.style.display='none'")
    pg.wait_for_timeout(400)
    pg.screenshot(path="docs/images/dashboard-overview.png",
                  clip={"x": 0, "y": 0, "width": 1440, "height": 760})
    b.close()
PYEOF

echo "rendering CLI output..."
{ echo "$ memor doctor"; echo ""; memor doctor 2>&1 | head -14; } \
  | "$PY" scripts/render_terminal_svg.py "$OUT/doctor.svg" "memor doctor"

{ echo "$ memor compression-worth"; echo ""
  memor compression-worth --days 3650 2>&1 | sed -n '/^REALIZED/,/^$/p'
  memor compression-worth --days 3650 2>&1 | sed -n '/^HOOK PATH/,/no invoice/p'
} | "$PY" scripts/render_terminal_svg.py "$OUT/compression-worth.svg" "memor compression-worth"

echo "done:"
ls -la "$OUT"
