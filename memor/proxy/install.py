"""Agent proxy install/uninstall helpers for Claude Code, Codex, Goose, Kimi, Cursor, Cline, and OpenCode."""
from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from memor.config import clear_proxy_upstream, set_proxy_agent, set_proxy_upstream
from memor.proxy.cline_install import (
    cline_paths,
    install_cline_proxy,
    strip_cline_proxy_urls,
    uninstall_cline_proxy,
)
from memor.proxy.cursor_install import (
    cursor_paths,
    install_cursor_proxy,
    strip_cursor_proxy_urls,
    uninstall_cursor_proxy,
)
from memor.proxy.goose_install import (
    _goose_paths,
    install_goose_proxy,
    strip_goose_proxy_urls,
    uninstall_goose_proxy,
)
from memor.proxy.kimi_install import (
    install_kimi_proxy,
    kimi_paths,
    strip_kimi_proxy_urls,
    uninstall_kimi_proxy,
)
from memor.proxy.opencode_install import (
    install_opencode_proxy,
    opencode_paths,
    strip_opencode_proxy_urls,
    uninstall_opencode_proxy,
)

_ANTHROPIC_DEFAULT = "https://api.anthropic.com/v1/messages"
_OPENAI_DEFAULT = "https://api.openai.com/v1/chat/completions"


def _get_memor_dir() -> Path:
    """Get the memor state directory."""
    return Path.home() / ".memor"


def _is_memor_localhost(url: str, port: int | None = None) -> bool:
    """True when url points at the Memor proxy on localhost."""
    if "127.0.0.1" not in url and "localhost" not in url:
        return False
    if port is None:
        return True
    return f":{port}" in url or f":{port}/" in url


def _normalize_anthropic_upstream(url: str) -> str:
    """Normalize a Claude ANTHROPIC_BASE_URL to a full /v1/messages URL."""
    url = url.rstrip("/")
    if url.endswith("/v1/messages"):
        return url
    if url.endswith("/v1"):
        return f"{url}/messages"
    return f"{url}/v1/messages"


def _normalize_openai_upstream(url: str) -> str:
    """Normalize a Codex openai_base_url to a full /v1/chat/completions URL."""
    url = url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/v1/chat/completions"


def _read_codex_openai_base_url(config_text: str) -> str | None:
    """Read openai_base_url from Codex config TOML text."""
    if not config_text.strip():
        return None
    try:
        import tomllib

        parsed = tomllib.loads(config_text)
        value = parsed.get("openai_base_url")
        return str(value) if value is not None else None
    except Exception:
        match = re.search(
            r'^\s*openai_base_url\s*=\s*["\']([^"\']+)["\']',
            config_text,
            re.MULTILINE,
        )
        return match.group(1) if match else None


def _capture_claude_upstream(settings: dict, port: int) -> tuple[str, str, str]:
    """Return (protocol, base_url, provider_name) from pre-proxy Claude settings."""
    url = str((settings.get("env") or {}).get("ANTHROPIC_BASE_URL", "")).strip()
    if url and not _is_memor_localhost(url, port):
        return ("anthropic", _normalize_anthropic_upstream(url), "anthropic")
    return ("anthropic", _ANTHROPIC_DEFAULT, "anthropic")


def _capture_codex_upstream(config_text: str, port: int) -> tuple[str, str, str]:
    """Return (protocol, base_url, provider_name) from pre-proxy Codex config."""
    url = (_read_codex_openai_base_url(config_text) or "").strip()
    if url and not _is_memor_localhost(url, port):
        return ("openai", _normalize_openai_upstream(url), "openai")
    return ("openai", _OPENAI_DEFAULT, "openai")


def _claude_paths() -> tuple[Path, Path, str]:
    memor_dir = _get_memor_dir()
    return (
        Path.home() / ".claude" / "settings.json",
        memor_dir / "proxy-backup-claude.json",
        json.dumps({}, indent=2),
    )


def _codex_paths() -> tuple[Path, Path, str]:
    memor_dir = _get_memor_dir()
    return (
        Path.home() / ".codex" / "config.toml",
        memor_dir / "proxy-backup-codex.toml",
        "",
    )


def _agent_paths(agent: str) -> tuple[Path, Path, str]:
    """(config path, proxy backup path, contents to record for a missing config)."""
    handler = AGENT_PROXY_HANDLERS.get(agent)
    if handler is None:
        raise ValueError(f"Unknown agent: {agent}")
    return handler.paths()


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

    backup_agent_config("claude")

    if config_path.exists():
        settings = json.loads(config_path.read_text())
    else:
        settings = {}

    protocol, base_url, provider_name = _capture_claude_upstream(settings, port)
    set_proxy_upstream(
        "claude",
        protocol=protocol,
        base_url=base_url,
        provider_name=provider_name,
    )

    if "env" not in settings:
        settings["env"] = {}
    settings["env"]["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(settings, indent=2) + "\n")

    set_proxy_agent("claude", True)
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

    backup_agent_config("codex")

    config_text = config_path.read_text() if config_path.exists() else ""

    protocol, base_url, provider_name = _capture_codex_upstream(config_text, port)
    set_proxy_upstream(
        "codex",
        protocol=protocol,
        base_url=base_url,
        provider_name=provider_name,
    )

    config_text = set_toml_top_level_key(
        config_text,
        "openai_base_url",
        f'openai_base_url = "http://127.0.0.1:{port}/v1"',
    )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text)

    set_proxy_agent("codex", True)
    register_mcp_codex()


def _uninstall_claude_proxy() -> None:
    config_path, backup_path, _ = _claude_paths()
    if backup_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(backup_path.read_text())
        backup_path.unlink()
    clear_proxy_upstream("claude")
    set_proxy_agent("claude", False)


def _uninstall_codex_proxy() -> None:
    config_path, backup_path, _ = _codex_paths()
    if backup_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(backup_path.read_text())
        backup_path.unlink()
    clear_proxy_upstream("codex")
    set_proxy_agent("codex", False)


def _strip_claude_proxy_urls(port: int) -> str:
    config_path, _, _ = _claude_paths()
    if not config_path.exists():
        return "claude: no config file to strip"
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
        return "claude: removed ANTHROPIC_BASE_URL"
    return "claude: no Memor ANTHROPIC_BASE_URL to strip"


def _strip_codex_proxy_urls(port: int) -> str:
    config_path, _, _ = _codex_paths()
    if not config_path.exists():
        return "codex: no config file to strip"
    text = config_path.read_text()
    needle = f'openai_base_url = "http://127.0.0.1:{port}/v1"'
    alt = f"openai_base_url = 'http://127.0.0.1:{port}/v1'"
    lines = [
        ln
        for ln in text.splitlines()
        if ln.strip() not in (needle, alt)
        and not (
            ln.strip().startswith("openai_base_url") and f"127.0.0.1:{port}" in ln
        )
    ]
    if len(lines) != len(text.splitlines()):
        config_path.write_text("\n".join(lines).rstrip("\n") + ("\n" if lines else ""))
        return "codex: removed openai_base_url"
    return "codex: no Memor openai_base_url to strip"


def _unknown_agent_strip(agent: str, _port: int) -> str:
    return f"{agent}: unknown agent"


def _unknown_agent_paths(_agent: str) -> tuple[Path, Path, str]:
    raise ValueError("Unknown agent")


@dataclass(frozen=True)
class AgentProxyHandler:
    """Registry entry for per-agent proxy install/uninstall."""

    install: Callable[[int], None]
    uninstall: Callable[[], None]
    strip: Callable[[int], str]
    paths: Callable[[], tuple[Path, Path, str]]


AGENT_PROXY_HANDLERS: dict[str, AgentProxyHandler] = {
    "claude": AgentProxyHandler(
        install=install_claude_proxy,
        uninstall=_uninstall_claude_proxy,
        strip=_strip_claude_proxy_urls,
        paths=_claude_paths,
    ),
    "codex": AgentProxyHandler(
        install=install_codex_proxy,
        uninstall=_uninstall_codex_proxy,
        strip=_strip_codex_proxy_urls,
        paths=_codex_paths,
    ),
    "goose": AgentProxyHandler(
        install=install_goose_proxy,
        uninstall=uninstall_goose_proxy,
        strip=strip_goose_proxy_urls,
        paths=_goose_paths,
    ),
    "kimi": AgentProxyHandler(
        install=install_kimi_proxy,
        uninstall=uninstall_kimi_proxy,
        strip=strip_kimi_proxy_urls,
        paths=kimi_paths,
    ),
    "cursor": AgentProxyHandler(
        install=lambda port: install_cursor_proxy(port),
        uninstall=uninstall_cursor_proxy,
        strip=strip_cursor_proxy_urls,
        paths=cursor_paths,
    ),
    "cline": AgentProxyHandler(
        install=lambda port: install_cline_proxy(port),
        uninstall=uninstall_cline_proxy,
        strip=strip_cline_proxy_urls,
        paths=cline_paths,
    ),
    "opencode": AgentProxyHandler(
        install=install_opencode_proxy,
        uninstall=uninstall_opencode_proxy,
        strip=strip_opencode_proxy_urls,
        paths=opencode_paths,
    ),
}


def install_agent_proxy(agent: str, port: int, *, upstream_url: str | None = None) -> None:
    """Install proxy for the given agent using the handler registry."""
    handler = AGENT_PROXY_HANDLERS.get(agent)
    if handler is None:
        raise ValueError(f"Unknown agent: {agent}")
    if agent == "goose":
        install_goose_proxy(port, upstream_url=upstream_url)
        return
    if agent == "cursor":
        install_cursor_proxy(port, upstream_url=upstream_url)
        return
    if agent == "cline":
        install_cline_proxy(port, upstream_url=upstream_url)
        return
    if agent == "opencode":
        install_opencode_proxy(port, upstream_url=upstream_url)
        return
    handler.install(port)


def uninstall_agent_proxy(agent: str) -> None:
    """Restore the backed-up config and clear the proxy_agent flag."""
    handler = AGENT_PROXY_HANDLERS.get(agent)
    if handler is None:
        raise ValueError(f"Unknown agent: {agent}")
    handler.uninstall()


def _strip_memor_proxy_urls(agent: str, port: int) -> str:
    """Best-effort remove Memor localhost base URLs when backup is missing."""
    handler = AGENT_PROXY_HANDLERS.get(agent)
    if handler is None:
        return f"{agent}: unknown agent"
    return handler.strip(port)


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
            handler = AGENT_PROXY_HANDLERS.get(agent)
            if handler is None:
                lines.append(f"{agent}: cleared flag (unknown agent)")
                continue
            try:
                config_path, backup_path, _ = handler.paths()
            except ValueError:
                lines.append(f"{agent}: cleared flag (unknown agent)")
                continue
            if backup_path.exists():
                handler.uninstall()
                lines.append(f"{agent}: restored config from backup; proxy flag cleared")
            else:
                detail = handler.strip(port)
                lines.append(f"{detail}; proxy flag cleared (no backup — verify config)")
        except Exception as e:
            lines.append(f"{agent}: failover error ({type(e).__name__}: {e}); clearing flag")
        finally:
            try:
                set_proxy_agent(agent, False)
                clear_proxy_upstream(agent)
            except Exception as e:
                lines.append(
                    f"{agent}: failed to clear proxy flag ({type(e).__name__}: {e})"
                )
    return lines


def register_mcp_claude() -> None:
    """Register memor_retrieve MCP server in Claude settings.json."""
    config_path = Path.home() / ".claude" / "settings.json"

    binary = shutil.which("memor-retrieve-mcp")
    if not binary:
        raise RuntimeError("memor-retrieve-mcp not found in PATH. Did you install memor-cli?")

    memor_dir = _get_memor_dir()
    memor_dir.mkdir(parents=True, exist_ok=True)
    backup_path = memor_dir / "mcp-backup-claude.json"

    if config_path.exists():
        backup_path.write_text(config_path.read_text())
        settings = json.loads(config_path.read_text())
    else:
        backup_path.write_text(json.dumps({}, indent=2))
        settings = {}

    if "mcpServers" not in settings:
        settings["mcpServers"] = {}

    settings["mcpServers"]["memor_retrieve"] = {
        "command": binary,
        "args": [],
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(settings, indent=2) + "\n")


def register_mcp_codex() -> None:
    """Register memor_retrieve MCP server in Codex config.toml."""
    config_path = Path.home() / ".codex" / "config.toml"

    binary = shutil.which("memor-retrieve-mcp")
    if not binary:
        raise RuntimeError("memor-retrieve-mcp not found in PATH. Did you install memor-cli?")

    memor_dir = _get_memor_dir()
    memor_dir.mkdir(parents=True, exist_ok=True)
    backup_path = memor_dir / "mcp-backup-codex.toml"

    if config_path.exists():
        backup_path.write_text(config_path.read_text())
        config_text = config_path.read_text()
    else:
        backup_path.write_text("")
        config_text = ""

    if "[mcp_servers.memor_retrieve]" in config_text:
        lines = config_text.splitlines()
        new_lines = []
        in_memor_section = False

        for line in lines:
            if line.strip().startswith("[mcp_servers.memor_retrieve]"):
                in_memor_section = True
                new_lines.append(line)
            elif in_memor_section and line.strip().startswith("["):
                in_memor_section = False
                new_lines.append(line)
            elif in_memor_section and line.strip().startswith("command"):
                new_lines.append(f'command = "{binary}"')
            else:
                new_lines.append(line)

        config_text = "\n".join(new_lines)
    else:
        mcp_section = f"""
[mcp_servers.memor_retrieve]
command = "{binary}"
"""
        config_text = config_text.rstrip() + "\n" + mcp_section

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text)
