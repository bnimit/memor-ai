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
    # The matcher must admit every tool the hook is willing to rewrite, or the
    # two disagree silently and the wider allowlist is dead code.
    from memor.posttool_compress import COMPRESSIBLE_TOOLS
    import re as _re
    # No IGNORECASE here, deliberately. Claude Code applies no flags, so a
    # matcher that needs one to pass is a matcher that fails in production.
    # Supplying the flag from the test is how `(?i)(bash|...)` -- a JavaScript
    # syntax error that matches nothing -- passed this assertion for 13 days
    # while the hook silently compressed nothing at all.
    for tool in COMPRESSIBLE_TOOLS:
        for spelling in (tool, tool.capitalize()):
            assert _re.fullmatch(group["matcher"], spelling), (
                f"matcher {group['matcher']!r} excludes {spelling!r}")
    # ...and must not spawn a process for the ones it always refuses.
    for tool in ("Read", "Edit", "Grep", "Glob", "Write"):
        assert not _re.fullmatch(group["matcher"], tool), tool
    # Anchored: an unanchored `ls` also matches `Tools`.
    for tool in ("Tools", "ToolSearch", "rebash"):
        assert not _re.search(group["matcher"], tool), tool

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


def _console_script() -> str | None:
    """Locate memor-posttool-compress, including inside the active venv.

    ``shutil.which`` searches only the ambient PATH, which does not contain the
    virtualenv's bin directory unless the venv was activated in the shell. Under
    ``.venv/bin/python -m pytest`` -- how CI and most contributors run the suite
    -- the script is installed and working, and the test skipped anyway. A skip
    that fires when the thing under test is present is worse than no test: it
    reports coverage that never ran.
    """
    found = shutil.which("memor-posttool-compress")
    if found:
        return found
    candidate = Path(sys.executable).parent / "memor-posttool-compress"
    return str(candidate) if candidate.exists() else None


@pytest.mark.skipif(_console_script() is None,
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
        [_console_script()],
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
    import re as _re
    from memor.posttool_compress import COMPRESSIBLE_TOOLS
    matcher = config["hooks"]["PostToolUse"][0]["matcher"]
    for tool in COMPRESSIBLE_TOOLS:
        assert _re.fullmatch(matcher, tool), tool
        assert _re.fullmatch(matcher, tool.capitalize()), tool


def test_matcher_compiles_in_javascript(sandbox_home, monkeypatch):
    """The matcher is compiled by Claude Code's JS engine, not by Python's.

    Python's `re` accepts `(?i)` and JavaScript's `RegExp` rejects it, so a
    Python-only test cannot see the failure at all: the assertion passes, the
    settings file is written, and the hook never runs. This is the check that
    would have caught it, and it is why it shells out to node rather than
    reasoning about regex dialects in the abstract.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available to check JavaScript regex semantics")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sandbox_home))
    from memor.cli import _install_posttool_compress
    from memor.posttool_compress import COMPRESSIBLE_TOOLS

    settings = sandbox_home / ".claude" / "settings.json"
    _install_posttool_compress(settings, "/bin/memor-posttool-compress")
    matcher = json.loads(settings.read_text())["hooks"]["PostToolUse"][0]["matcher"]

    should_match = sorted(COMPRESSIBLE_TOOLS) + [
        t.capitalize() for t in sorted(COMPRESSIBLE_TOOLS)]
    should_not = ["Read", "Edit", "Grep", "Glob", "Write", "Tools", "ToolSearch"]
    script = (
        "const re = new RegExp(%s);\n"
        "const yes = %s, no = %s;\n"
        "for (const t of yes) if (!re.test(t)) { console.log('MISSING ' + t);"
        " process.exit(1); }\n"
        "for (const t of no) if (re.test(t)) { console.log('EXTRA ' + t);"
        " process.exit(1); }\n"
        "console.log('OK');\n"
    ) % (json.dumps(matcher), json.dumps(should_match), json.dumps(should_not))

    proc = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"matcher {matcher!r} is not usable by Claude Code: "
        f"{proc.stdout.strip()}{proc.stderr.strip()}")


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


# --- doctor's wiring check -------------------------------------------------
#
# The hook failed for 13 days with every service reporting healthy, because
# nothing verified that the registered hook could actually be selected. These
# cover that gap: a check that only passes on a correct config is worth little
# if it does not also fail on the exact config that broke.

def _settings(tmp_path, matcher, command="/bin/memor-posttool-compress"):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"PostToolUse": [
        {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}
    ]}}))
    return p


def test_doctor_flags_the_matcher_that_actually_broke(tmp_path):
    """The regression, verbatim: a Python-only inline flag in a JS regex."""
    from memor.cli import check_posttool_wiring

    problems = check_posttool_wiring(
        _settings(tmp_path, "(?i)(agentgrep|bash|bg|ls|todo)"))
    assert problems, "the matcher that silently disabled the hook was accepted"
    assert "never runs" in problems[0]
    assert "install-compress-hook" in problems[0]


def test_doctor_flags_a_matcher_that_misses_tools(tmp_path):
    from memor.cli import check_posttool_wiring
    from memor.posttool_compress import COMPRESSIBLE_TOOLS

    problems = check_posttool_wiring(_settings(tmp_path, "^([Bb][Aa][Ss][Hh])$"))
    assert problems
    missed = [t for t in COMPRESSIBLE_TOOLS if t != "bash"]
    assert all(t in problems[0] for t in missed)


def test_doctor_flags_a_hook_binary_that_vanished(tmp_path):
    from memor.cli import _ci_pattern, check_posttool_wiring
    from memor.posttool_compress import COMPRESSIBLE_TOOLS

    problems = check_posttool_wiring(_settings(
        tmp_path, _ci_pattern(COMPRESSIBLE_TOOLS),
        command="/nonexistent/memor-posttool-compress"))
    assert any("no longer" in p for p in problems)


def test_doctor_is_silent_on_a_correct_install(tmp_path, sandbox_home, monkeypatch):
    """No news is the point: a check that cries wolf gets ignored."""
    import shutil

    from memor.cli import _install_posttool_compress, check_posttool_wiring

    real = shutil.which("sh")  # any file that exists, standing in for the hook
    hook = tmp_path / "memor-posttool-compress"
    shutil.copy(real, hook)
    settings = tmp_path / ".claude" / "settings.json"
    _install_posttool_compress(settings, str(hook))
    assert check_posttool_wiring(settings) == []


def test_doctor_stays_quiet_when_the_hook_is_not_installed(tmp_path):
    """Not installing is a choice, and nagging about it trains people to skim."""
    from memor.cli import check_posttool_wiring

    assert check_posttool_wiring(tmp_path / "absent.json") == []
    other = tmp_path / "settings.json"
    other.write_text(json.dumps({"hooks": {"PostToolUse": [
        {"matcher": "(?i)somebody-elses-hook",
         "hooks": [{"command": "/bin/not-ours"}]}
    ]}}))
    assert check_posttool_wiring(other) == []
