"""Every read-only CLI command executes without raising.

`memor install-compress-hook` shipped crashing on NameError because its body
was never invoked, only the helper beneath it. pyflakes now catches unbound
names statically; this catches the runtime equivalent -- a command that
imports fine and explodes when actually called.

Only commands that read are included. Anything that installs, deletes or
mutates state is exercised in its own test with an explicit sandbox, because a
smoke test that quietly rewrote a settings file would be worse than no smoke
test at all.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from memor.cli import app

#: Read-only commands, with arguments where required.
READ_ONLY_COMMANDS = [
    ["version"],
    ["compression-worth"],
    ["recall-worth"],
    ["service", "status"],
]


@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".memor").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)

    from memor.store.sqlite_store import SqliteStore

    SqliteStore(str(home / ".memor" / "memor.db"), dim=16)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.mark.parametrize("argv", READ_ONLY_COMMANDS, ids=lambda a: " ".join(a))
def test_read_only_command_does_not_raise(argv, sandbox_home):
    result = CliRunner().invoke(app, argv)
    # A command may legitimately exit non-zero (nothing configured yet); it may
    # not raise. NameError and AttributeError are the signatures of a body that
    # was never executed before shipping.
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(
            f"memor {' '.join(argv)} raised "
            f"{type(result.exception).__name__}: {result.exception}"
        )


def test_help_lists_the_compression_commands(sandbox_home):
    """The commands the README tells users to run must be discoverable."""
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    registered = {c.name for c in app.registered_commands}
    for name in ("install-compress-hook", "uninstall-compress-hook",
                 "compression-worth"):
        assert name in registered


def test_compression_worth_runs_on_an_empty_ledger(sandbox_home):
    """A fresh install must produce a report, not a traceback."""
    result = CliRunner().invoke(app, ["compression-worth"])
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "proxied requests" in result.stdout or "compression" in result.stdout.lower()


def test_readme_commands_all_exist():
    """Documentation that names a command the CLI lacks is a broken promise."""
    import re

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    referenced = set(re.findall(r"^memor ([a-z][a-z-]+)", readme, re.M))
    registered = {c.name for c in app.registered_commands}
    groups = {g.name for g in app.registered_groups}
    # "can" appears in prose ("memor can distill sessions with...").
    referenced.discard("can")
    missing = sorted(referenced - registered - groups)
    assert not missing, f"README references non-existent commands: {missing}"
