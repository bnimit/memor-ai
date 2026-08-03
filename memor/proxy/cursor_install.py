"""Cursor IDE proxy install helpers (BYOK base URL override)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from memor.config import clear_proxy_upstream, set_proxy_agent, set_proxy_upstream
from memor.proxy.vscode_settings import (
    first_url_value,
    load_settings_json,
    set_settings_keys,
    vscode_user_settings_path,
    write_settings_json,
)

CURSOR_DOCS = "https://docs.cursor.com/settings/models"

# Keys observed across Cursor versions / community guides (best-effort).
CURSOR_OPENAI_URL_KEYS = (
    "openai.baseUrl",
    "cursor.openai.baseUrl",
    "cursor.general.openAiBaseUrl",
)
CURSOR_ANTHROPIC_URL_KEYS = (
    "cursor.anthropic.baseUrl",
    "anthropic.baseUrl",
)

_OPENAI_DEFAULT = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_DEFAULT = "https://api.anthropic.com/v1/messages"


class CursorConfigError(Exception):
    """Raised when Cursor proxy install cannot proceed."""


def _get_memor_dir() -> Path:
    return Path.home() / ".memor"


def cursor_paths() -> tuple[Path, Path, str]:
    return (
        vscode_user_settings_path("Cursor"),
        _get_memor_dir() / "proxy-backup-cursor.json",
        "{}\n",
    )


def _is_memor_localhost(url: str, port: int | None = None) -> bool:
    if "127.0.0.1" not in url and "localhost" not in url:
        return False
    if port is None:
        return True
    return f":{port}" in url or f":{port}/" in url


def _normalize_openai_upstream(url: str) -> str:
    url = url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/v1/chat/completions"


def _normalize_anthropic_upstream(url: str) -> str:
    url = url.rstrip("/")
    if url.endswith("/v1/messages"):
        return url
    if url.endswith("/v1"):
        return f"{url}/messages"
    return f"{url}/v1/messages"


def proxy_openai_base_url(port: int) -> str:
    """OpenAI-compatible override URL (path prefix attributes ledger to cursor)."""
    return f"http://127.0.0.1:{port}/cursor/v1"


def proxy_anthropic_base_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/cursor"


def discover_cursor_upstream(
    settings_path: Path,
    *,
    port: int | None = None,
    upstream_url: str | None = None,
) -> tuple[str, str, str]:
    """Return (protocol, normalized upstream URL, provider_name)."""
    if upstream_url and str(upstream_url).strip():
        url = str(upstream_url).strip()
        protocol = "anthropic" if "/messages" in url or "anthropic" in url else "openai"
        normalize = _normalize_anthropic_upstream if protocol == "anthropic" else _normalize_openai_upstream
        return protocol, normalize(url), "custom"

    settings = load_settings_json(settings_path)
    openai_url = first_url_value(settings, CURSOR_OPENAI_URL_KEYS)
    anthropic_url = first_url_value(settings, CURSOR_ANTHROPIC_URL_KEYS)

    if openai_url and not _is_memor_localhost(openai_url, port):
        return "openai", _normalize_openai_upstream(openai_url), "openai"
    if anthropic_url and not _is_memor_localhost(anthropic_url, port):
        return "anthropic", _normalize_anthropic_upstream(anthropic_url), "anthropic"

    # Default: OpenAI path (BYOK override is OpenAI-shaped in Cursor).
    return "openai", _OPENAI_DEFAULT, "openai"


def install_cursor_proxy(port: int, *, upstream_url: str | None = None) -> list[str]:
    """Install Cursor proxy settings and return manual follow-up lines."""
    from memor.proxy.install import backup_agent_config

    config_path, _, _ = cursor_paths()
    backup_agent_config("cursor")

    protocol, base_url, provider_name = discover_cursor_upstream(
        config_path, port=port, upstream_url=upstream_url,
    )
    set_proxy_upstream(
        "cursor",
        protocol=protocol,
        base_url=base_url,
        provider_name=provider_name,
    )

    settings = load_settings_json(config_path)
    proxy_url = (
        proxy_anthropic_base_url(port)
        if protocol == "anthropic"
        else proxy_openai_base_url(port)
    )
    key = CURSOR_ANTHROPIC_URL_KEYS[0] if protocol == "anthropic" else CURSOR_OPENAI_URL_KEYS[0]
    settings = set_settings_keys(settings, {key: proxy_url})
    write_settings_json(config_path, settings)
    set_proxy_agent("cursor", True)

    return manual_setup_lines(port, protocol)


def manual_setup_lines(port: int, protocol: str) -> list[str]:
    """UI steps Cursor may still require (override is not always settings.json-only)."""
    openai_url = proxy_openai_base_url(port)
    anthropic_url = proxy_anthropic_base_url(port)
    lines = [
        "Cursor BYOK override (required for compression on custom models):",
        "  Settings → Models → enable Override OpenAI Base URL",
        f"  OpenAI override: {openai_url}",
        f"  Anthropic override (if used): {anthropic_url}",
        "  Use a BYOK/custom model — Cursor Pro/Composer subscription models",
        "  do not flow through your base URL.",
        "  For subscription Composer Shell compression (no BYOK):",
        "    memor install-cursor-compress-hooks",
        f"  Docs: {CURSOR_DOCS}",
    ]
    if protocol == "anthropic":
        lines.insert(3, f"  Active upstream captured as Anthropic ({anthropic_url})")
    else:
        lines.insert(3, f"  Active upstream captured as OpenAI-compatible ({openai_url})")
    return lines


@dataclass
class CursorStackResult:
    lines: list[str] = field(default_factory=list)
    byok_ok: bool = False
    aborted: bool = False


def explain_cursor_install() -> list[str]:
    return [
        "This enables Cursor support:",
        "  • Memory recall (hooks, if missing)",
        "  • Shell output compression hooks",
        "  • BYOK proxy on 127.0.0.1:8421 (custom models)",
    ]


def _ensure_memory_hooks() -> list[str]:
    lines: list[str] = []
    try:
        import shutil

        from memor.cli import _install_hook_logic

        hook_bin = shutil.which("memor-hook")
        if not hook_bin:
            lines.append("Memory hooks: memor-hook not on PATH — run: memor install-hook")
            return lines
        settings = Path.home() / ".claude" / "settings.json"
        if settings.exists() and "memor-hook" in settings.read_text():
            lines.append("Memory hooks: already installed (~/.claude/settings.json)")
            return lines
        _install_hook_logic(settings, hook_bin)
        lines.append(f"Memory hooks: installed memor-hook → {settings}")
    except Exception as exc:
        lines.append(f"Memory hooks: skipped ({exc})")
    return lines


def _ensure_shell_hooks() -> list[str]:
    try:
        from memor.cursor_compress_install import install_cursor_compress_hooks

        return install_cursor_compress_hooks()
    except Exception as exc:
        return [f"Shell compress hooks: failed ({exc})"]


def install_cursor_stack(
    *,
    byok_port: int,
    upstream_url: str | None = None,
    yes: bool = False,
    confirm: Callable[[str], bool] | None = None,
) -> CursorStackResult:
    """Install the Cursor stack: memory hooks + Shell compress hooks + BYOK proxy.

    Compression reaches Cursor through the shell hooks, which crush tool output
    before Cursor ever ingests it. Subscription Composer traffic is deliberately
    not touched — see docs; intercepting it locally was measured unreachable.
    """
    from memor import service

    result = CursorStackResult()
    ask = confirm or (lambda _m: yes)

    result.lines.extend(explain_cursor_install())
    if not yes and not ask("Continue with Cursor install (hooks + BYOK)?"):
        result.aborted = True
        result.lines.append("Aborted.")
        return result

    manual = install_cursor_proxy(byok_port, upstream_url=upstream_url)
    result.byok_ok = True
    result.lines.append("BYOK proxy: Cursor base URL keys updated")
    result.lines.extend(manual)

    result.lines.extend(_ensure_memory_hooks())
    result.lines.extend(_ensure_shell_hooks())
    result.lines.append(service.install(with_dashboard=True, with_proxy=True))
    return result


#: Keys written by the removed Cursor wire MITM. Stripped on uninstall so an
#: upgrading user is never left pointing Cursor at a proxy that no longer runs.
LEGACY_WIRE_KEYS = (
    "http.proxy",
    "http.proxySupport",
    "http.proxyStrictSSL",
    "http.noProxy",
    "http.proxyBypassList",
    "cursor.general.disableHttp2",
)


def strip_legacy_wire_settings() -> bool:
    """Remove leftover wire proxy keys from Cursor settings.json.

    Only strips when ``http.proxy`` points at localhost — a user's own corporate
    proxy must survive untouched.
    """
    from memor.proxy.vscode_settings import (
        load_settings_json,
        remove_settings_keys,
        write_settings_json,
    )

    config_path, _, _ = cursor_paths()
    if not config_path.exists():
        return False
    settings = load_settings_json(config_path)
    proxy = str(settings.get("http.proxy") or "").strip().lower()
    if proxy and not (
        proxy.startswith("http://127.0.0.1:") or proxy.startswith("http://localhost:")
    ):
        return False
    settings, changed = remove_settings_keys(settings, LEGACY_WIRE_KEYS)
    if changed:
        write_settings_json(config_path, settings)
    return changed


def uninstall_cursor_proxy() -> None:
    from memor.config import clear_cursor_wire_keys

    # Drop legacy wire keys first (in case the backup predates the wire install).
    strip_legacy_wire_settings()
    clear_cursor_wire_keys()

    config_path, backup_path, _ = cursor_paths()
    if backup_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(backup_path.read_text())
        backup_path.unlink()
    else:
        # Backup missing — still strip BYOK localhost URLs if present.
        from memor.config import proxy_port as get_proxy_port

        strip_cursor_proxy_urls(get_proxy_port())
    clear_proxy_upstream("cursor")
    set_proxy_agent("cursor", False)

    # Remove any launchd/systemd unit stranded by the removed wire feature.
    try:
        from memor import service
        import os
        import subprocess

        if service._is_macos():
            path = service._plist_path(service.CURSOR_WIRE_LABEL)
            if path.exists():
                subprocess.run(
                    ["launchctl", "bootout", f"gui/{os.getuid()}", str(path)],
                    capture_output=True,
                )
                path.unlink(missing_ok=True)
        else:
            path = service._unit_path("memor-cursor-wire")
            if path.exists():
                subprocess.run(
                    ["systemctl", "--user", "disable", "--now", "memor-cursor-wire"],
                    capture_output=True,
                )
                path.unlink(missing_ok=True)
                subprocess.run(
                    ["systemctl", "--user", "daemon-reload"], capture_output=True
                )
    except Exception:
        pass


def strip_cursor_proxy_urls(port: int) -> str:
    config_path, _, _ = cursor_paths()
    if not config_path.exists():
        return "cursor: no config file to strip"

    settings = load_settings_json(config_path)
    proxy_openai = proxy_openai_base_url(port)
    proxy_anthropic = proxy_anthropic_base_url(port)
    changed = False
    for key in CURSOR_OPENAI_URL_KEYS + CURSOR_ANTHROPIC_URL_KEYS:
        value = settings.get(key)
        if value in (proxy_openai, proxy_anthropic) or _is_memor_localhost(str(value or ""), port):
            settings.pop(key, None)
            changed = True

    if changed:
        write_settings_json(config_path, settings)
        return "cursor: removed Memor base URL keys"
    return "cursor: no Memor base URL to strip"
