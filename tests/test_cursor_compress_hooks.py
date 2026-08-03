"""Tests for Cursor Shell compression hooks."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from memor.cursor_compress_hook import (
    build_wrapped_shell_command,
    handle_pre_tool_use,
    run_compress_exec,
)
from memor.cursor_compress_install import (
    HOOK_MARKER,
    install_cursor_compress_hooks,
    uninstall_cursor_compress_hooks,
)


@pytest.fixture
def mock_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (tmp_path / ".memor").mkdir()
    return tmp_path


def test_handle_pre_tool_use_wraps_shell(monkeypatch):
    monkeypatch.setattr(
        "memor.cursor_compress_hook.compress_exec_bin",
        lambda: "/usr/local/bin/memor-compress-exec",
    )
    request = {
        "hook_event_name": "preToolUse",
        "tool_name": "Shell",
        "tool_input": {
            "command": "npm test",
            "working_directory": "/project",
        },
    }
    result = handle_pre_tool_use(request)
    assert result["permission"] == "allow"
    wrapped = result["updated_input"]["command"]
    assert "memor-compress-exec" in wrapped
    assert "npm test" in wrapped
    assert "/project" in wrapped


def test_handle_pre_tool_use_skips_non_shell():
    request = {"tool_name": "Read", "tool_input": {"path": "foo.py"}}
    assert handle_pre_tool_use(request) == {"permission": "allow"}


def test_handle_pre_tool_use_skips_already_wrapped(monkeypatch):
    monkeypatch.setattr(
        "memor.cursor_compress_hook.compress_exec_bin",
        lambda: "/usr/local/bin/memor-compress-exec",
    )
    request = {
        "tool_name": "Shell",
        "tool_input": {"command": "memor-compress-exec --cwd /tmp -- echo hi"},
    }
    assert handle_pre_tool_use(request) == {"permission": "allow"}


def test_handle_pre_tool_use_fail_open_without_exec(monkeypatch):
    monkeypatch.setattr("memor.cursor_compress_hook.compress_exec_bin", lambda: None)
    request = {
        "tool_name": "Shell",
        "tool_input": {"command": "npm test"},
    }
    assert handle_pre_tool_use(request) == {"permission": "allow"}


def test_build_wrapped_shell_command_quotes():
    cmd = build_wrapped_shell_command(
        'echo "hello world"',
        "/tmp/my project",
        "/opt/memor-compress-exec",
    )
    assert "/opt/memor-compress-exec" in cmd
    assert "hello world" in cmd


def test_run_compress_exec_compresses_log_output(tmp_path):
    script = tmp_path / "run.sh"
    script.write_text(
        "#!/bin/sh\n"
        "i=0\n"
        "while [ $i -lt 50 ]; do echo \"INFO line $i\"; i=$((i+1)); done\n"
        "echo ERROR: something failed\n"
    )
    script.chmod(0o755)
    code = run_compress_exec(cwd=str(tmp_path), command=f"sh {script.name}")
    assert code == 0


def test_install_and_uninstall_cursor_compress_hooks(mock_home, monkeypatch, tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    hook = fake_bin / "memor-cursor-compress-hook"
    hook.write_text("#!/bin/sh\necho ok\n")
    hook.chmod(0o755)
    exec_bin = fake_bin / "memor-compress-exec"
    exec_bin.write_text("#!/bin/sh\necho ok\n")
    exec_bin.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    lines = install_cursor_compress_hooks()
    assert any("installed" in line.lower() for line in lines)

    hooks_path = mock_home / ".cursor" / "hooks.json"
    hooks = json.loads(hooks_path.read_text())
    pre = hooks["hooks"]["preToolUse"]
    assert any(HOOK_MARKER in entry["command"] for entry in pre)
    assert pre[-1]["matcher"] == "Shell"

    rules_path = mock_home / ".cursor" / "rules" / "memor-compress.mdc"
    assert rules_path.exists()

    msg = uninstall_cursor_compress_hooks()
    assert "restored" in msg or "removed" in msg


def test_install_updates_existing_memor_entry(mock_home, monkeypatch, tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("memor-cursor-compress-hook", "memor-compress-exec"):
        path = fake_bin / name
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    hooks_path = mock_home / ".cursor" / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "preToolUse": [
                        {
                            "command": "/old/memor-cursor-compress-hook",
                            "matcher": "Shell",
                        }
                    ]
                },
            }
        )
        + "\n"
    )

    install_cursor_compress_hooks()
    hooks = json.loads(hooks_path.read_text())
    command = hooks["hooks"]["preToolUse"][0]["command"]
    assert HOOK_MARKER in command or str(fake_bin) in command
    assert hooks["hooks"]["preToolUse"][0]["command"] != "/old/memor-cursor-compress-hook"


def test_hook_main_prints_json(monkeypatch, capsys):
    import io

    monkeypatch.setattr(
        "memor.cursor_compress_hook.compress_exec_bin",
        lambda: "/usr/local/bin/memor-compress-exec",
    )
    payload = json.dumps(
        {
            "hook_event_name": "preToolUse",
            "tool_name": "Shell",
            "tool_input": {"command": "git status"},
        }
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    from memor.cursor_compress_hook import hook_main

    with pytest.raises(SystemExit) as exc:
        hook_main()
    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert "updated_input" in out
