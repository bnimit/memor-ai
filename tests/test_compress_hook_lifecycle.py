"""The install -> use -> measure lifecycle, end to end in a sandbox.

Each piece of this was verified separately: the installer writes settings, the
hook rewrites output, the ledger records a saving, the report renders it. That
is not the same as the whole thing working, and the gap between the two is
where this project's worst bug of the day lived -- 35 rows sitting in a real
ledger, each individually correct, that together described nothing real.

So this drives the actual sequence a user performs, against a HOME that is a
temporary directory:

    memor install-compress-hook  ->  Claude Code runs Bash  ->  memor
    compression-worth

and asserts the number at the end came from the payload at the start. It uses
the real installer, the real settings file shape, the real hook binary as a
subprocess, and the real report renderer. Nothing is stubbed except HOME.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def sandbox_home(tmp_path):
    """A HOME with an initialised memor database and no settings file."""
    home = tmp_path / "home"
    (home / ".memor").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)

    from memor.store.sqlite_store import SqliteStore

    SqliteStore(str(home / ".memor" / "memor.db"), dim=16)
    return home


def _noisy_build_log(lines: int = 400) -> str:
    """Output shaped like a real build: repetitive, with the failure at the end."""
    return "\n".join(
        f"INFO  compiling module_{i % 9}.ts ({i} of {lines}) in {i % 40}ms"
        for i in range(lines)
    ) + "\nERROR  type error in module_3.ts: expected string, got number\n"


def _run_hook(home: Path, payload: dict) -> dict:
    """Invoke the hook exactly as Claude Code does: JSON on stdin, JSON out."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    proc = subprocess.run(
        [sys.executable, "-c", "from memor.posttool_compress import main; main()"],
        input=json.dumps(payload),
        capture_output=True, text=True, cwd=str(REPO), env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def test_install_use_measure_lifecycle(sandbox_home, monkeypatch):
    """A user installs the hook, runs a command, and sees the saving reported."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sandbox_home))

    # 1. Install. The user runs `memor install-compress-hook`.
    from memor.cli import _install_posttool_compress

    settings = sandbox_home / ".claude" / "settings.json"
    _install_posttool_compress(settings, "/bin/memor-posttool-compress")

    config = json.loads(settings.read_text())
    group = config["hooks"]["PostToolUse"][0]
    assert group["matcher"] == "Bash"

    # 2. Use. Claude Code runs a build and fires the hook with the result.
    original = _noisy_build_log()
    response = _run_hook(sandbox_home, {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "npm run build"},
        "tool_response": {"stdout": original, "stderr": "",
                          "interrupted": False, "isImage": False},
        "session_id": "lifecycle-1",
    })

    shown = response["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    assert len(shown) < len(original)
    # The failure is the reason the command was run; it must survive.
    assert "type error in module_3.ts" in shown

    # 3. Measure. The saving reaches the ledger the report reads.
    db = sqlite3.connect(str(sandbox_home / ".memor" / "memor.db"))
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute(
        "SELECT provider, session_id, tokens_before, tokens_after "
        "FROM proxy_savings")]
    db.close()

    assert len(rows) == 1
    assert rows[0]["provider"] == "hook"
    assert rows[0]["session_id"] == "lifecycle-1"
    assert rows[0]["tokens_after"] < rows[0]["tokens_before"]

    # 4. Report. The figure a user reads traces back to the payload above.
    from memor.compression_worth import load_savings_rows, summarize_savings

    summary = summarize_savings(
        load_savings_rows(str(sandbox_home / ".memor" / "memor.db"), days=1))
    assert summary.hook_requests == 1
    assert summary.hook_before == rows[0]["tokens_before"]
    assert summary.hook_saved > 0


def test_uninstall_stops_the_rewrite(sandbox_home, monkeypatch):
    """Uninstalling must remove the hook, not merely stop reporting it."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sandbox_home))

    from memor.cli import _install_posttool_compress, _uninstall_posttool_compress

    settings = sandbox_home / ".claude" / "settings.json"
    _install_posttool_compress(settings, "/bin/memor-posttool-compress")
    assert _uninstall_posttool_compress(settings) is True

    config = json.loads(settings.read_text())
    assert "PostToolUse" not in config.get("hooks", {})


def test_lifecycle_leaves_the_real_home_untouched(sandbox_home):
    """The sandbox must be a real sandbox.

    Asserting this inside the lifecycle test is what distinguishes a genuine
    end-to-end run from one that quietly wrote somewhere else -- the exact
    failure that put 35 fixture rows into a live database.
    """
    _run_hook(sandbox_home, {
        "tool_name": "Bash",
        "tool_response": {"stdout": _noisy_build_log(), "stderr": "",
                          "interrupted": False, "isImage": False},
        "session_id": "sandbox-check",
    })

    db = sqlite3.connect(str(sandbox_home / ".memor" / "memor.db"))
    assert db.execute("SELECT COUNT(*) FROM proxy_savings").fetchone()[0] == 1
    db.close()
    # The autouse conftest guard independently fails this test if the real
    # ledger grew; this asserts the write went where it was supposed to.


@pytest.mark.skipif(shutil.which("memor-posttool-compress") is None,
                    reason="entry point not installed in this environment")
def test_installed_console_script_is_wired(sandbox_home):
    """The console script the installer points at must actually exist and run.

    The installer writes a path resolved with shutil.which; if the entry point
    is missing or misnamed, every hook invocation fails silently and the user
    sees no compression and no error.
    """
    env = dict(os.environ)
    env["HOME"] = str(sandbox_home)
    proc = subprocess.run(
        [shutil.which("memor-posttool-compress")],
        input=json.dumps({
            "tool_name": "Bash",
            "tool_response": {"stdout": _noisy_build_log(), "stderr": "",
                              "interrupted": False, "isImage": False},
        }),
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


def test_cli_install_command_runs(sandbox_home, monkeypatch):
    """Invoke the CLI command itself, not just the helper beneath it.

    The unit tests called _install_posttool_compress directly and all passed
    while `memor install-compress-hook` crashed with NameError: shutil was
    used in the command body but never imported. Testing the helper proves the
    settings-file logic; only invoking the command proves the command.
    """
    from typer.testing import CliRunner

    from memor.cli import app

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sandbox_home))
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep
                       + os.environ.get("PATH", ""))

    result = CliRunner().invoke(app, ["install-compress-hook"])
    if shutil.which("memor-posttool-compress") is None:
        # Without the console script the command must fail cleanly, not crash.
        assert result.exit_code == 1
        assert "not found on PATH" in (result.stdout + str(result.exception or ""))
        return

    assert result.exit_code == 0, result.output
    assert result.exception is None
    config = json.loads((sandbox_home / ".claude" / "settings.json").read_text())
    assert config["hooks"]["PostToolUse"][0]["matcher"] == "Bash"


def test_cli_uninstall_command_runs(sandbox_home, monkeypatch):
    from typer.testing import CliRunner

    from memor.cli import _install_posttool_compress, app

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sandbox_home))
    _install_posttool_compress(
        sandbox_home / ".claude" / "settings.json", "/bin/memor-posttool-compress")

    result = CliRunner().invoke(app, ["uninstall-compress-hook"])
    assert result.exit_code == 0, result.output
    assert result.exception is None
    config = json.loads((sandbox_home / ".claude" / "settings.json").read_text())
    assert "PostToolUse" not in config.get("hooks", {})


def test_cli_rejects_unsupported_agent(sandbox_home, monkeypatch):
    from typer.testing import CliRunner

    from memor.cli import app

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sandbox_home))
    result = CliRunner().invoke(app, ["install-compress-hook", "--agent", "kimi"])
    assert result.exit_code == 1
    assert not (sandbox_home / ".claude" / "settings.json").exists()
