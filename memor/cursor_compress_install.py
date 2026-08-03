"""Install Cursor preToolUse Shell compression hooks."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

HOOK_MARKER = "memor-cursor-compress-hook"
RULE_MARKER = "<!-- memor-compress-rules -->"

RULES_MDC = f"""---
description: "Memor compresses verbose Shell tool output before it enters agent context"
globs: **/*
alwaysApply: true
---
{RULE_MARKER}
Shell commands in Agent mode are wrapped by Memor's Cursor hook to compress
logs, test output, and large terminal text before they reach the model.
Do not unwrap or bypass the wrapper. Read and Edit tools are unchanged.
"""


def _memor_dir() -> Path:
    return Path.home() / ".memor"


def cursor_hooks_path() -> Path:
    return Path.home() / ".cursor" / "hooks.json"


def cursor_rules_path() -> Path:
    return Path.home() / ".cursor" / "rules" / "memor-compress.mdc"


def hooks_backup_path() -> Path:
    return _memor_dir() / "cursor-compress-hooks-backup.json"


def rules_backup_path() -> Path:
    return _memor_dir() / "cursor-compress-rules-backup.mdc"


def _hook_command() -> str:
    binary = shutil.which("memor-cursor-compress-hook")
    if not binary:
        raise RuntimeError(
            "memor-cursor-compress-hook not found on PATH. Install memor-cli first."
        )
    return binary


def _load_hooks(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "hooks": {}}
    return json.loads(path.read_text())


def _is_memor_pre_tool_use(entry: dict) -> bool:
    command = str(entry.get("command") or "")
    return HOOK_MARKER in command


def _merge_pre_tool_use(hooks_data: dict, hook_command: str) -> bool:
    hooks = hooks_data.setdefault("hooks", {})
    pre_tool_use = list(hooks.get("preToolUse") or [])
    for entry in pre_tool_use:
        if _is_memor_pre_tool_use(entry):
            entry["command"] = hook_command
            entry["matcher"] = "Shell"
            entry["timeout"] = entry.get("timeout", 5)
            hooks["preToolUse"] = pre_tool_use
            return False

    pre_tool_use.append(
        {
            "command": hook_command,
            "matcher": "Shell",
            "timeout": 5,
        }
    )
    hooks["preToolUse"] = pre_tool_use
    return True


def _strip_pre_tool_use(hooks_data: dict) -> bool:
    hooks = hooks_data.get("hooks") or {}
    pre_tool_use = list(hooks.get("preToolUse") or [])
    filtered = [entry for entry in pre_tool_use if not _is_memor_pre_tool_use(entry)]
    if len(filtered) == len(pre_tool_use):
        return False
    if filtered:
        hooks["preToolUse"] = filtered
    else:
        hooks.pop("preToolUse", None)
    if not hooks:
        hooks_data.pop("hooks", None)
    return True


def install_cursor_compress_hooks() -> list[str]:
    """Install global Cursor Shell compression hook. Returns status lines."""
    hook_command = _hook_command()
    if not shutil.which("memor-compress-exec"):
        raise RuntimeError(
            "memor-compress-exec not found on PATH. Install memor-cli first."
        )

    hooks_path = cursor_hooks_path()
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = hooks_backup_path()
    _memor_dir().mkdir(parents=True, exist_ok=True)

    if hooks_path.exists() and not backup_path.exists():
        backup_path.write_text(hooks_path.read_text())

    hooks_data = _load_hooks(hooks_path)
    added = _merge_pre_tool_use(hooks_data, hook_command)
    hooks_path.write_text(json.dumps(hooks_data, indent=2) + "\n")

    rules_path = cursor_rules_path()
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_backup = rules_backup_path()
    if rules_path.exists() and not rules_backup.exists():
        rules_backup.write_text(rules_path.read_text())
    rules_path.write_text(RULES_MDC)

    lines = [
        "Cursor Shell compression hook installed.",
        f"  hooks.json: {hooks_path}",
        f"  rules: {rules_path}",
        "  Applies to subscription Composer / Agent Shell tool output.",
        "  Restart Cursor or start a new Agent chat to load hooks.",
    ]
    if added:
        lines.insert(1, "  Added preToolUse matcher=Shell entry.")
    else:
        lines.insert(1, "  Updated existing Memor preToolUse entry.")
    return lines


def uninstall_cursor_compress_hooks() -> str:
    """Restore hooks/rules backups and remove Memor compress hook."""
    hooks_path = cursor_hooks_path()
    backup_path = hooks_backup_path()
    rules_path = cursor_rules_path()
    rules_backup = rules_backup_path()

    if backup_path.exists():
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text(backup_path.read_text())
        backup_path.unlink()
        msg = "cursor compress hooks: restored hooks.json backup"
    elif hooks_path.exists():
        hooks_data = _load_hooks(hooks_path)
        if _strip_pre_tool_use(hooks_data):
            if hooks_data.get("hooks"):
                hooks_path.write_text(json.dumps(hooks_data, indent=2) + "\n")
            else:
                hooks_path.unlink(missing_ok=True)
            msg = "cursor compress hooks: removed Memor preToolUse entry"
        else:
            msg = "cursor compress hooks: no Memor preToolUse entry found"
    else:
        msg = "cursor compress hooks: no hooks.json to change"

    if rules_backup.exists():
        if rules_path.exists():
            rules_path.write_text(rules_backup.read_text())
        rules_backup.unlink()
    elif rules_path.exists() and RULE_MARKER in rules_path.read_text():
        rules_path.unlink()

    return msg
