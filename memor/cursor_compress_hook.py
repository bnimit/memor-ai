"""Cursor preToolUse hook — wrap Shell commands to compress terminal output."""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from memor.compress import compress_text

MEMOR_COMPRESS_EXEC_MARKER = "memor-compress-exec"
MEMOR_COMPRESS_EXEC_FLAG = "--memor-compress-exec"


def compress_exec_bin() -> str | None:
    return shutil.which("memor-compress-exec")


def is_already_wrapped(command: str) -> bool:
    cmd = command or ""
    return MEMOR_COMPRESS_EXEC_MARKER in cmd or MEMOR_COMPRESS_EXEC_FLAG in cmd


def resolve_working_directory(request: dict, tool_input: dict) -> str:
    for key in ("working_directory", "cwd"):
        value = tool_input.get(key) or request.get(key)
        if value and str(value).strip():
            return str(value)
    return str(Path.cwd())


def build_wrapped_shell_command(command: str, working_directory: str, exec_bin: str) -> str:
    """Run command via memor-compress-exec so stdout seen by the agent is compressed."""
    return (
        f"{shlex.quote(exec_bin)} "
        f"--cwd {shlex.quote(working_directory)} "
        f"-- {shlex.quote(command)}"
    )


def handle_pre_tool_use(request: dict) -> dict:
    """Return Cursor preToolUse hook JSON. Fail-open unless we wrap Shell."""
    tool_name = request.get("tool_name") or ""
    if tool_name != "Shell":
        return {"permission": "allow"}

    tool_input = dict(request.get("tool_input") or {})
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return {"permission": "allow"}
    if is_already_wrapped(command):
        return {"permission": "allow"}

    exec_bin = compress_exec_bin()
    if not exec_bin:
        return {"permission": "allow"}

    cwd = resolve_working_directory(request, tool_input)
    tool_input["command"] = build_wrapped_shell_command(command, cwd, exec_bin)
    return {"permission": "allow", "updated_input": tool_input}


def run_compress_exec(*, cwd: str, command: str, timeout: float = 600.0) -> int:
    """Execute shell command and print compressed combined stdout/stderr."""
    workdir = Path(cwd).expanduser()
    if not workdir.is_dir():
        print(f"memor-compress-exec: invalid cwd: {cwd}", file=sys.stderr)
        return 1

    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"memor-compress-exec: command timed out after {int(timeout)}s", file=sys.stderr)
        return 124

    output = completed.stdout or ""
    if completed.stderr:
        if output and not output.endswith("\n"):
            output += "\n"
        output += completed.stderr

    if not output.strip():
        if completed.returncode != 0:
            print(f"[exit {completed.returncode}]", file=sys.stderr)
        return completed.returncode

    result = compress_text(output)
    sys.stdout.write(result.text)
    if not result.text.endswith("\n"):
        sys.stdout.write("\n")

    if result.tokens_before > result.tokens_after and not result.passthrough:
        saved = result.tokens_before - result.tokens_after
        print(
            f"[memor: shell output compressed "
            f"{result.tokens_before}->{result.tokens_after} tokens, saved {saved}]",
            file=sys.stderr,
        )
    return completed.returncode


def hook_main() -> None:
    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        raise SystemExit(1)

    event = request.get("hook_event_name") or ""
    if event and event != "preToolUse":
        print(json.dumps({"permission": "allow"}))
        raise SystemExit(0)

    print(json.dumps(handle_pre_tool_use(request)))
    raise SystemExit(0)


def compress_exec_entry() -> None:
    raise SystemExit(compress_exec_main())


def compress_exec_main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    cwd = str(Path.cwd())
    command_parts: list[str] = []

    i = 0
    while i < len(args):
        if args[i] == "--cwd" and i + 1 < len(args):
            cwd = args[i + 1]
            i += 2
            continue
        if args[i] == "--":
            command_parts = args[i + 1 :]
            break
        i += 1

    if not command_parts:
        print("usage: memor-compress-exec --cwd <dir> -- <shell-command>", file=sys.stderr)
        return 2

    command = " ".join(command_parts)
    return run_compress_exec(cwd=cwd, command=command)
