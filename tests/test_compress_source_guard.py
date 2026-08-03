"""Source code must never reach a crusher.

The log crusher deletes lines it judges repetitive. On logs that is the whole
point; on source code it silently returns a mutilated file and the agent then
edits against content that was never there. These tests pin the guard.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from memor.compress import compress_text
from memor.compress.detect import detect_content_type, looks_like_source

REPO = Path(__file__).resolve().parent.parent

PYTHON = '''\
from __future__ import annotations
import os

class Thing:
    def run(self, x):
        for i in range(10):
            if x > i:
                return i
        return None
'''

JS = '''\
const state = {count: 0};
function bump(n) {
  for (let i = 0; i < n; i++) {
    state.count += 1;
  }
  return state.count;
}
export default bump;
'''

HTML_WITH_WARN = """\
<!DOCTYPE html>
<html><head><style>
  :root { --ok: #0a0; --warn: #fa0; --error: #f00; }
  .led { color: var(--warn); }
  .bad { color: var(--error); }
  .good { color: var(--ok); }
</style></head>
<body><div id="app"></div></body></html>
"""

GO = '''\
package main

import "fmt"

func main() {
	for i := 0; i < 3; i++ {
		fmt.Println(i)
	}
}
'''

SHELL = """\
#!/usr/bin/env bash
set -euo pipefail
for f in *.txt; do
  echo "$f"
done
"""


# --- detection ---------------------------------------------------------------


@pytest.mark.parametrize(
    "name,text",
    [("python", PYTHON), ("js", JS), ("html", HTML_WITH_WARN), ("go", GO), ("shell", SHELL)],
)
def test_source_is_detected_as_source(name, text):
    assert detect_content_type(text) == "source", name


def test_css_warn_variable_no_longer_reads_as_a_log():
    """Regression: `var(--warn)` matched \\bWARN\\b and sent HTML to the log crusher."""
    assert detect_content_type(HTML_WITH_WARN) != "log"


def test_looks_like_source_needs_two_signals():
    """One stray keyword in prose must not trip the guard."""
    prose = "We had to import the data by hand because the export was broken.\n" * 5
    assert looks_like_source(prose) is False


def test_looks_like_source_accepts_shebang_alone():
    assert looks_like_source("#!/usr/bin/env python\nprint(1)\n") is True


def test_empty_text_is_not_source():
    assert looks_like_source("") is False


# --- the guarantee -----------------------------------------------------------


@pytest.mark.parametrize(
    "name,text",
    [("python", PYTHON), ("js", JS), ("html", HTML_WITH_WARN), ("go", GO), ("shell", SHELL)],
)
def test_compress_is_identity_on_source(name, text):
    result = compress_text(text)
    assert result.text == text, f"{name}: source was modified"
    assert result.tokens_after == result.tokens_before


@pytest.mark.parametrize(
    "path",
    ["memor/service.py", "memor/dashboard/static/index.html", "memor/compress/detect.py"],
)
def test_real_repo_files_round_trip_unchanged(path):
    """The files whose reads were actually being shredded."""
    text = (REPO / path).read_text()
    result = compress_text(text)
    assert result.text == text
    assert len(result.text.splitlines()) == len(text.splitlines())


# --- the guard must not steal work from the real compressors ----------------


def test_grep_output_over_code_still_compresses():
    """Search results contain code fragments; they must stay compressible."""
    text = "\n".join(f"memor/mod{i}.py:{i}:    return value_{i}" for i in range(200))
    assert detect_content_type(text) == "search"
    result = compress_text(text)
    assert result.tokens_after < result.tokens_before


def test_real_logs_still_compress():
    text = "\n".join(
        f"2026-08-03 10:00:{i % 60:02d} INFO worker heartbeat ok id={i}" for i in range(400)
    )
    assert detect_content_type(text) == "log"
    result = compress_text(text)
    assert result.tokens_after < result.tokens_before * 0.5
