"""The version is written in two files and read from a third.

They drifted once already: pyproject said 0.12.0 while the changelog recorded
that 0.12.0 "was stamped on 4 August but never published". A release whose
number does not match its notes cannot be reasoned about after the fact, and
`memor service status` compares versions to detect a stale proxy, so a wrong
number there degrades a diagnostic rather than announcing itself.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _package_version_from_source() -> str:
    """Read the version from the source text, not the imported module.

    `import memor` can serve stale bytecode: CPython validates a .pyc by the
    source's mtime *second* and size, so an edit that keeps the byte length and
    lands within the same second is invisible to it. That happened while
    building this guard -- the module reported a version that existed nowhere on
    disk. Reading the file makes the check immune to it.
    """
    m = re.search(r'__version__ = "([^"]+)"', (ROOT / "memor" / "__init__.py").read_text())
    assert m, "memor/__init__.py has no __version__"
    return m.group(1)


def _pyproject_version() -> str:
    m = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    assert m, "pyproject.toml has no version"
    return m.group(1)


def test_package_and_pyproject_versions_match():
    assert _package_version_from_source() == _pyproject_version()


def test_changelog_documents_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    heading = f"## [{_package_version_from_source()}]"
    assert heading in changelog, (
        f"CHANGELOG.md has no {heading} section. Bumping the version without "
        "writing down what changed leaves users unable to tell whether an "
        "upgrade concerns them."
    )


def test_released_version_is_not_left_under_unreleased():
    """The current version must sit above the Unreleased marker, not inside it."""
    changelog = (ROOT / "CHANGELOG.md").read_text()
    unreleased = changelog.index("## [Unreleased]")
    current = changelog.index(f"## [{_package_version_from_source()}]")
    assert current > unreleased, "version heading must follow the Unreleased section"


def test_imported_module_matches_its_source():
    """Catch stale bytecode rather than silently trusting the import.

    CPython validates a cached .pyc against the source's mtime second and byte
    size. An edit preserving both is invisible, and the interpreter then serves
    a version string that exists nowhere on disk -- observed while writing these
    tests. Anything importing memor for its version (service status, the
    dashboard) would report the phantom value.
    """
    import subprocess
    import sys

    # A fresh interpreter, because an already-imported module in this process
    # reflects whatever the file said when the suite started -- earlier tests may
    # legitimately have rewritten it. Only a new process re-reads the cache.
    out = subprocess.run(
        [sys.executable, "-c", "import memor; print(memor.__version__)"],
        capture_output=True, text=True, cwd=ROOT, check=True).stdout.strip()
    assert out == _package_version_from_source(), (
        f"imported memor.__version__ ({out}) disagrees with memor/__init__.py "
        f"({_package_version_from_source()}); stale bytecode -- rm -rf memor/__pycache__"
    )
