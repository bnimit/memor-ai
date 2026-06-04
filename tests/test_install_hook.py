import json
import sys
from pathlib import Path
from memor.cli import _install_hook_logic


def test_install_hook_creates_settings(tmp_path):
    settings_path = tmp_path / "settings.json"
    hook_path = "/path/to/memor-hook.py"
    _install_hook_logic(settings_path, hook_path)
    data = json.loads(settings_path.read_text())
    groups = data["hooks"]["UserPromptSubmit"]
    assert len(groups) == 1
    assert groups[0]["matcher"] == ""
    assert len(groups[0]["hooks"]) == 1
    assert hook_path in groups[0]["hooks"][0]["command"]
    assert sys.executable in groups[0]["hooks"][0]["command"]
    assert groups[0]["hooks"][0]["timeout"] == 5000


def test_install_hook_preserves_existing(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "model": "opus",
        "hooks": {
            "UserPromptSubmit": [
                {"matcher": "", "hooks": [
                    {"type": "command", "command": "my-other-hook.sh", "timeout": 1000}
                ]}
            ]
        }
    }))
    hook_path = "/path/to/memor-hook.py"
    _install_hook_logic(settings_path, hook_path)
    data = json.loads(settings_path.read_text())
    assert data["model"] == "opus"
    groups = data["hooks"]["UserPromptSubmit"]
    assert len(groups) == 2
    assert groups[0]["hooks"][0]["command"] == "my-other-hook.sh"
    assert "memor-hook" in groups[1]["hooks"][0]["command"]


def test_install_hook_idempotent(tmp_path):
    settings_path = tmp_path / "settings.json"
    hook_path = "/path/to/memor-hook.py"
    _install_hook_logic(settings_path, hook_path)
    _install_hook_logic(settings_path, hook_path)
    data = json.loads(settings_path.read_text())
    groups = data["hooks"]["UserPromptSubmit"]
    memor_groups = [g for g in groups
                    if any("memor-hook" in h.get("command", "") for h in g.get("hooks", []))]
    assert len(memor_groups) == 1


def test_install_hook_updates_existing_memor_entry(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "hooks": {
            "UserPromptSubmit": [
                {"matcher": "", "hooks": [
                    {"type": "command", "command": "python3 /old/memor-hook.py", "timeout": 1000}
                ]}
            ]
        }
    }))
    hook_path = "/new/memor-hook.py"
    _install_hook_logic(settings_path, hook_path)
    data = json.loads(settings_path.read_text())
    groups = data["hooks"]["UserPromptSubmit"]
    assert len(groups) == 1
    assert "/new/memor-hook.py" in groups[0]["hooks"][0]["command"]
    assert groups[0]["hooks"][0]["timeout"] == 5000
