"""Shared helpers for VS Code / Cursor User settings.json proxy installs."""
from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path


def vscode_user_settings_path(app_name: str) -> Path:
    """Return User/settings.json for a VS Code-family app (Code, Cursor, …)."""
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return home / "Library" / "Application Support" / app_name / "User" / "settings.json"
    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / app_name / "User" / "settings.json"
    return home / ".config" / app_name / "User" / "settings.json"


def load_settings_json(path: Path) -> dict:
    """Load settings.json, tolerating trailing commas."""
    if not path.exists():
        return {}
    text = path.read_text()
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        cleaned = re.sub(r",(\s*[}\]])", r"\1", text)
        parsed = json.loads(cleaned)
    return parsed if isinstance(parsed, dict) else {}


def write_settings_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def first_url_value(settings: dict, keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty URL string among dotted settings keys."""
    for key in keys:
        value = settings.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def set_settings_keys(settings: dict, updates: dict[str, str]) -> dict:
    """Return settings with scalar keys updated."""
    merged = dict(settings)
    merged.update(updates)
    return merged


def remove_settings_keys(settings: dict, keys: tuple[str, ...]) -> tuple[dict, bool]:
    """Remove keys from settings; return (new_settings, changed)."""
    merged = dict(settings)
    changed = False
    for key in keys:
        if key in merged:
            merged.pop(key, None)
            changed = True
    return merged, changed
