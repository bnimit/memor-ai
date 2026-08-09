"""Static guard against names used but never bound.

`memor install-compress-hook` shipped crashing with ``NameError: shutil``: the
command body called ``shutil.which`` and cli.py never imported it. Every unit
test passed, because they exercised the helper underneath the command rather
than the command itself.

A test per command body would be better and is not always practical; this is
the cheap net that catches the whole class in one pass. It is the same check a
linter runs, pinned here so it runs in CI with everything else.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _pyflakes_cmd() -> list[str] | None:
    """Locate pyflakes however it is available, or None to skip."""
    try:
        import pyflakes  # noqa: F401
        return [sys.executable, "-m", "pyflakes"]
    except ImportError:
        pass
    found = shutil.which("pyflakes")
    return [found] if found else None


def test_no_undefined_names_in_package():
    cmd = _pyflakes_cmd()
    if cmd is None:
        pytest.skip("pyflakes is not installed")

    proc = subprocess.run(
        cmd + [str(REPO / "memor")], capture_output=True, text=True,
    )
    undefined = [
        line for line in proc.stdout.splitlines()
        if "undefined name" in line.lower()
    ]
    assert not undefined, (
        "names used but never bound:\n  " + "\n  ".join(undefined)
    )
