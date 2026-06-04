import json
from pathlib import Path
from memor.cli import _install_hook_logic


def test_install_hook_creates_settings(tmp_path):
    settings_path = tmp_path / "settings.json"
    hook_path = "/path/to/memor-hook.py"
    _install_hook_logic(settings_path, hook_path)
    data = json.loads(settings_path.read_text())
    hooks = data["hooks"]["UserPromptSubmit"]
    assert len(hooks) == 1
    assert hooks[0]["command"] == f"python3 {hook_path}"
    assert hooks[0]["timeout"] == 5000


def test_install_hook_preserves_existing(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "model": "opus",
        "hooks": {
            "UserPromptSubmit": [
                {"type": "command", "command": "my-other-hook.sh", "timeout": 1000}
            ]
        }
    }))
    hook_path = "/path/to/memor-hook.py"
    _install_hook_logic(settings_path, hook_path)
    data = json.loads(settings_path.read_text())
    assert data["model"] == "opus"
    hooks = data["hooks"]["UserPromptSubmit"]
    assert len(hooks) == 2
    assert hooks[0]["command"] == "my-other-hook.sh"
    assert "memor-hook" in hooks[1]["command"]


def test_install_hook_idempotent(tmp_path):
    settings_path = tmp_path / "settings.json"
    hook_path = "/path/to/memor-hook.py"
    _install_hook_logic(settings_path, hook_path)
    _install_hook_logic(settings_path, hook_path)
    data = json.loads(settings_path.read_text())
    hooks = data["hooks"]["UserPromptSubmit"]
    memor_hooks = [h for h in hooks if "memor-hook" in h["command"]]
    assert len(memor_hooks) == 1


def test_install_hook_updates_existing_memor_entry(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "hooks": {
            "UserPromptSubmit": [
                {"type": "command", "command": "python3 /old/memor-hook.py", "timeout": 1000}
            ]
        }
    }))
    hook_path = "/new/memor-hook.py"
    _install_hook_logic(settings_path, hook_path)
    data = json.loads(settings_path.read_text())
    hooks = data["hooks"]["UserPromptSubmit"]
    assert len(hooks) == 1
    assert "/new/memor-hook.py" in hooks[0]["command"]
    assert hooks[0]["timeout"] == 5000
