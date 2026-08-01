"""Agent proxy install/uninstall helpers for Claude Code and Codex."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from memor.config import set_proxy_agent


def _get_memor_dir() -> Path:
    """Get the memor state directory."""
    return Path.home() / ".memor"


def _agent_paths(agent: str) -> tuple[Path, Path, str]:
    """(config path, proxy backup path, contents to record for a missing config)."""
    memor_dir = _get_memor_dir()
    if agent == "claude":
        return (
            Path.home() / ".claude" / "settings.json",
            memor_dir / "proxy-backup-claude.json",
            json.dumps({}, indent=2),
        )
    if agent == "codex":
        return (
            Path.home() / ".codex" / "config.toml",
            memor_dir / "proxy-backup-codex.toml",
            "",
        )
    raise ValueError(f"Unknown agent: {agent}")


def backup_agent_config(agent: str) -> Path:
    """Back up the agent's config file to ~/.memor/proxy-backup-<agent>.json or .toml.
    
    An existing backup is left untouched: re-running install would otherwise
    capture the config we already rewrote, and uninstall would then "restore"
    the proxied state permanently. Uninstall clears the backup.
    
    Returns the backup file path.
    """
    memor_dir = _get_memor_dir()
    memor_dir.mkdir(parents=True, exist_ok=True)
    
    config_path, backup_path, empty_contents = _agent_paths(agent)
    if backup_path.exists():
        return backup_path
    
    backup_path.write_text(
        config_path.read_text() if config_path.exists() else empty_contents
    )
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
    
    # Register MCP server
    register_mcp_claude()


def set_toml_top_level_key(text: str, key: str, value_line: str) -> str:
    """Set a bare TOML key, keeping it above the first table header.
    
    Appending to the end of the file would put the key inside whichever table
    happens to be last — valid TOML, but a different key path, so the setting
    would silently stop applying.
    """
    lines = text.splitlines()
    out: list[str] = []
    replaced = False
    in_table = False
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_table = True
        if not in_table and "=" in stripped and stripped.split("=", 1)[0].strip() == key:
            out.append(value_line)
            replaced = True
            continue
        out.append(line)
    
    if not replaced:
        first_table = next(
            (i for i, line in enumerate(out) if line.strip().startswith("[")),
            len(out),
        )
        out.insert(first_table, value_line)
    
    return "\n".join(out).rstrip("\n") + "\n"


def install_codex_proxy(port: int) -> None:
    """Install Codex proxy by setting openai_base_url in config.toml."""
    config_path = Path.home() / ".codex" / "config.toml"
    
    # Backup first
    backup_agent_config("codex")
    
    config_text = config_path.read_text() if config_path.exists() else ""
    config_text = set_toml_top_level_key(
        config_text,
        "openai_base_url",
        f'openai_base_url = "http://127.0.0.1:{port}/v1"',
    )
    
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text)
    
    # Set proxy_agent flag
    set_proxy_agent("codex", True)
    
    # Register MCP server
    register_mcp_codex()


def uninstall_agent_proxy(agent: str) -> None:
    """Restore the backed-up config and clear the proxy_agent flag."""
    config_path, backup_path, _ = _agent_paths(agent)
    
    if backup_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(backup_path.read_text())
        # Consumed: the next install captures a fresh pre-proxy snapshot.
        backup_path.unlink()
    
    # Clear proxy_agent flag
    set_proxy_agent(agent, False)


def _strip_memor_proxy_urls(agent: str, port: int) -> str:
    """Best-effort remove Memor localhost base URLs when backup is missing."""
    config_path, _, _ = _agent_paths(agent)
    if not config_path.exists():
        return f"{agent}: no config file to strip"
    if agent == "claude":
        settings = json.loads(config_path.read_text() or "{}")
        env = dict(settings.get("env") or {})
        url = str(env.get("ANTHROPIC_BASE_URL", ""))
        if f"127.0.0.1:{port}" in url or f"localhost:{port}" in url:
            env.pop("ANTHROPIC_BASE_URL", None)
            if env:
                settings["env"] = env
            else:
                settings.pop("env", None)
            config_path.write_text(json.dumps(settings, indent=2) + "\n")
            return f"{agent}: removed ANTHROPIC_BASE_URL"
        return f"{agent}: no Memor ANTHROPIC_BASE_URL to strip"
    if agent == "codex":
        text = config_path.read_text()
        needle = f'openai_base_url = "http://127.0.0.1:{port}/v1"'
        alt = f"openai_base_url = 'http://127.0.0.1:{port}/v1'"
        lines = [
            ln for ln in text.splitlines()
            if ln.strip() not in (needle, alt)
            and not (
                ln.strip().startswith("openai_base_url")
                and f"127.0.0.1:{port}" in ln
            )
        ]
        if len(lines) != len(text.splitlines()):
            config_path.write_text("\n".join(lines).rstrip("\n") + ("\n" if lines else ""))
            return f"{agent}: removed openai_base_url"
        return f"{agent}: no Memor openai_base_url to strip"
    return f"{agent}: unknown agent"


def failover_proxy_agents(reason: str = "") -> list[str]:
    """Restore pre-proxy agent configs (uninstall-shaped) for all opted-in agents.

    Best-effort and never raises: per-agent I/O or JSON errors are caught so
    other agents still fail over. Each agent's `proxy_agents` flag is cleared
    in a ``finally`` block. Consumes backups when restore succeeds.
    """
    from memor.config import load_config, proxy_port as get_port
    port = get_port()
    agents = load_config().get("proxy_agents") or {}
    enabled = [a for a, on in agents.items() if on]
    lines: list[str] = []
    if reason:
        lines.append(f"proxy failover: {reason}")
    if not enabled:
        lines.append("proxy failover: no proxy_agents enabled")
        return lines
    for agent in enabled:
        try:
            try:
                config_path, backup_path, _ = _agent_paths(agent)
            except ValueError:
                lines.append(f"{agent}: cleared flag (unknown agent)")
                continue
            if backup_path.exists():
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(backup_path.read_text())
                backup_path.unlink()
                lines.append(f"{agent}: restored config from backup; proxy flag cleared")
            else:
                detail = _strip_memor_proxy_urls(agent, port)
                lines.append(f"{detail}; proxy flag cleared (no backup — verify config)")
        except Exception as e:
            lines.append(f"{agent}: failover error ({type(e).__name__}: {e}); clearing flag")
        finally:
            try:
                set_proxy_agent(agent, False)
            except Exception as e:
                lines.append(
                    f"{agent}: failed to clear proxy flag ({type(e).__name__}: {e})"
                )
    return lines


def register_mcp_claude() -> None:
    """Register memor_retrieve MCP server in Claude settings.json."""
    config_path = Path.home() / ".claude" / "settings.json"
    
    # Find the binary path
    binary = shutil.which("memor-retrieve-mcp")
    if not binary:
        raise RuntimeError("memor-retrieve-mcp not found in PATH. Did you install memor-cli?")
    
    # Create backup
    memor_dir = _get_memor_dir()
    memor_dir.mkdir(parents=True, exist_ok=True)
    backup_path = memor_dir / "mcp-backup-claude.json"
    
    if config_path.exists():
        backup_path.write_text(config_path.read_text())
        settings = json.loads(config_path.read_text())
    else:
        backup_path.write_text(json.dumps({}, indent=2))
        settings = {}
    
    # Add MCP server
    if "mcpServers" not in settings:
        settings["mcpServers"] = {}
    
    settings["mcpServers"]["memor_retrieve"] = {
        "command": binary,
        "args": []
    }
    
    # Write back
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(settings, indent=2) + "\n")


def register_mcp_codex() -> None:
    """Register memor_retrieve MCP server in Codex config.toml."""
    config_path = Path.home() / ".codex" / "config.toml"
    
    # Find the binary path
    binary = shutil.which("memor-retrieve-mcp")
    if not binary:
        raise RuntimeError("memor-retrieve-mcp not found in PATH. Did you install memor-cli?")
    
    # Create backup
    memor_dir = _get_memor_dir()
    memor_dir.mkdir(parents=True, exist_ok=True)
    backup_path = memor_dir / "mcp-backup-codex.toml"
    
    if config_path.exists():
        backup_path.write_text(config_path.read_text())
        config_text = config_path.read_text()
    else:
        backup_path.write_text("")
        config_text = ""
    
    # Check if memor_retrieve section already exists
    if "[mcp_servers.memor_retrieve]" in config_text:
        # Update existing section
        lines = config_text.splitlines()
        new_lines = []
        in_memor_section = False
        
        for line in lines:
            if line.strip().startswith("[mcp_servers.memor_retrieve]"):
                in_memor_section = True
                new_lines.append(line)
            elif in_memor_section and line.strip().startswith("["):
                # End of memor section
                in_memor_section = False
                new_lines.append(line)
            elif in_memor_section and line.strip().startswith("command"):
                new_lines.append(f'command = "{binary}"')
            else:
                new_lines.append(line)
        
        config_text = "\n".join(new_lines)
    else:
        # Append new section
        mcp_section = f"""
[mcp_servers.memor_retrieve]
command = "{binary}"
"""
        config_text = config_text.rstrip() + "\n" + mcp_section
    
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text)
