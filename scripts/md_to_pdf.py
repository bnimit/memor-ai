#!/usr/bin/env python3
"""Render a markdown document to a print-quality PDF via headless Chrome.

Chrome is used rather than weasyprint/pandoc because it is already installed on
this machine and its print engine handles the table-heavy layout of these
reports without extra system libraries.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import markdown

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
body {
  font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 10.2pt; line-height: 1.5; color: #1a1a1a; max-width: none;
}
h1 { font-size: 21pt; margin: 0 0 2pt; letter-spacing: -0.4pt; }
h1 + p { color: #555; margin-top: 0; }
h2 {
  font-size: 14pt; margin: 20pt 0 7pt; padding-bottom: 3pt;
  border-bottom: 1.5px solid #1a1a1a; page-break-after: avoid;
}
h3 { font-size: 11.4pt; margin: 14pt 0 5pt; page-break-after: avoid; }
h4 { font-size: 10.4pt; margin: 11pt 0 4pt; page-break-after: avoid; }
p, li { orphans: 3; widows: 3; }
table {
  border-collapse: collapse; width: 100%; margin: 9pt 0;
  font-size: 8.9pt; page-break-inside: avoid;
}
th {
  background: #f0f0f0; text-align: left; padding: 5pt 7pt;
  border-bottom: 1.2px solid #999; font-weight: 600;
}
td { padding: 4.5pt 7pt; border-bottom: 0.5px solid #ddd; vertical-align: top; }
tr:nth-child(even) td { background: #fafafa; }
code {
  font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 8.6pt;
  background: #f2f2f2; padding: 1px 4px; border-radius: 3px;
}
pre {
  background: #f7f7f7; border-left: 2.5px solid #999; padding: 8pt 11pt;
  overflow-x: auto; page-break-inside: avoid; margin: 9pt 0;
}
pre code { background: none; padding: 0; font-size: 8.5pt; line-height: 1.42; }
blockquote {
  border-left: 2.5px solid #ccc; margin: 9pt 0; padding: 2pt 0 2pt 12pt;
  color: #444; font-style: italic;
}
hr { border: none; border-top: 0.8px solid #ddd; margin: 16pt 0; }
strong { font-weight: 600; }
a { color: #1a1a1a; text-decoration: none; }
ul, ol { padding-left: 20pt; }
li { margin: 2.5pt 0; }
h2 { page-break-before: auto; }
"""

HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>{css}</style></head><body>{body}</body></html>"""


def render(md_path: Path, pdf_path: Path) -> Path:
    # Chrome needs absolute file:// URIs, and --print-to-pdf resolves its
    # output relative to Chrome's cwd rather than ours.
    md_path = md_path.resolve()
    pdf_path = pdf_path.resolve()
    text = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(
        text, extensions=["tables", "fenced_code", "toc", "sane_lists"]
    )
    html_path = pdf_path.with_suffix(".html")
    html_path.write_text(
        HTML.format(title=md_path.stem, css=CSS, body=body), encoding="utf-8"
    )

    subprocess.run(
        [
            CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}", html_path.as_uri(),
        ],
        check=True, capture_output=True, timeout=120,
    )
    html_path.unlink(missing_ok=True)
    return pdf_path


if __name__ == "__main__":
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".pdf")
    result = render(src, out)
    print(f"{result}  ({result.stat().st_size:,} bytes)")
