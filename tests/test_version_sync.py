"""The version is written in two files and read from a third.

They drifted once already: pyproject said 0.12.0 while the changelog recorded
that 0.12.0 "was stamped on 4 August but never published". A release whose
number does not match its notes cannot be reasoned about after the fact, and
`memor service status` compares versions to detect a stale proxy, so a wrong
number there degrades a diagnostic rather than announcing itself.
"""
import pathlib
import re

import memor

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    m = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    assert m, "pyproject.toml has no version"
    return m.group(1)


def test_package_and_pyproject_versions_match():
    assert memor.__version__ == _pyproject_version()


def test_changelog_documents_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    heading = f"## [{memor.__version__}]"
    assert heading in changelog, (
        f"CHANGELOG.md has no {heading} section. Bumping the version without "
        "writing down what changed leaves users unable to tell whether an "
        "upgrade concerns them."
    )


def test_released_version_is_not_left_under_unreleased():
    """The current version must sit above the Unreleased marker, not inside it."""
    changelog = (ROOT / "CHANGELOG.md").read_text()
    unreleased = changelog.index("## [Unreleased]")
    current = changelog.index(f"## [{memor.__version__}]")
    assert current > unreleased, "version heading must follow the Unreleased section"
