"""Agent proxy install/uninstall helpers for Claude Code and Codex."""
from __future__ import annotations

import json
from pathlib import Path
from memor.config import set_proxy_agent


def _get_memor_dir() -> Path:
    """Get the memor state directory."""
    return Path.home() / ".memor"


def backup_agent_config(agent: str) -> Path:
    """Back up the agent's config file to ~/.memor/proxy-backup-<agent>.json or .toml.
    
    Returns the backup file path.
    """
    memor_dir = _get_memor_dir()
    memor_dir.mkdir(parents=True, exist_ok=True)
    
    if agent == "claude":
        config_path = Path.home() / ".claude" / "settings.json"
        backup_path = memor_dir / "proxy-backup-claude.json"
        
        if config_path.exists():
            backup_path.write_text(config_path.read_text())
        else:
            backup_path.write_text(json.dumps({}, indent=2))
    
    elif agent == "codex":
        config_path = Path.home() / ".codex" / "config.toml"
        backup_path = memor_dir / "proxy-backup-codex.toml"
        
        if config_path.exists():
            backup_path.write_text(config_path.read_text())
        else:
            backup_path.write_text("")
    
    else:
        raise ValueError(f"Unknown agent: {agent}")
    
    return backup_path


def install_claude_proxy(port: int) -> None:
    """Install Claude Code proxy by setting ANTHROPIC_BASE_URL in settings.json."""
    config_path = Path.home() / ".claude" / "settings.json"
    
    # Backup first
    backup_agent_config("claude")
    
    # Load or create settings
    if config_path.exists():
        settings = json.loads(config_path.read_text())
    else:
        settings = {}
    
    # Merge in the proxy base URL
    if "env" not in settings:
        settings["env"] = {}
    settings["env"]["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    
    # Write back
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(settings, indent=2) + "\n")
    
    # Set proxy_agent flag
    set_proxy_agent("claude", True)


def install_codex_proxy(port: int) -> None:
    """Install Codex proxy by setting openai_base_url in config.toml.
    
    Uses a simple approach: append or update openai_base_url at the top level.
    """
    config_path = Path.home() / ".codex" / "config.toml"
    
    # Backup first
    backup_agent_config("codex")
    
    # Load existing config
    if config_path.exists():
        config_text = config_path.read_text()
    else:
        config_text = ""
    
    # Parse and update (simple approach: check if key exists, update or append)
    lines = config_text.splitlines()
    base_url_line = f'openai_base_url = "http://127.0.0.1:{port}/v1"'
    
    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith("openai_base_url"):
            new_lines.append(base_url_line)
            found = True
        else:
            new_lines.append(line)
    
    if not found:
        # Append at the end
        new_lines.append(base_url_line)
    
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(new_lines) + "\n")
    
    # Set proxy_agent flag
    set_proxy_agent("codex", True)


def uninstall_agent_proxy(agent: str) -> None:
    """Restore the backed-up config and clear the proxy_agent flag."""
    memor_dir = _get_memor_dir()
    
    if agent == "claude":
        backup_path = memor_dir / "proxy-backup-claude.json"
        config_path = Path.home() / ".claude" / "settings.json"
        
        if backup_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(backup_path.read_text())
    
    elif agent == "codex":
        backup_path = memor_dir / "proxy-backup-codex.toml"
        config_path = Path.home() / ".codex" / "config.toml"
        
        if backup_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(backup_path.read_text())
    
    else:
        raise ValueError(f"Unknown agent: {agent}")
    
    # Clear proxy_agent flag
    set_proxy_agent(agent, False)
