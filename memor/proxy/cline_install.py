"""Cline VS Code extension proxy install helpers."""
from __future__ import annotations

from pathlib import Path

from memor.config import clear_proxy_upstream, set_proxy_agent, set_proxy_upstream
from memor.proxy.vscode_settings import (
    first_url_value,
    load_settings_json,
    set_settings_keys,
    vscode_user_settings_path,
    write_settings_json,
)

CLINE_DOCS = "https://docs.cline.bot/provider-config/openai-compatible"

CLINE_OPENAI_KEYS = (
    "cline.openAiBaseUrl",
    "cline.openAiCompatible.baseUrl",
    "cline.openAiCompatibleBaseUrl",
)
CLINE_ANTHROPIC_KEYS = (
    "cline.anthropicBaseUrl",
)

_OPENAI_DEFAULT = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_DEFAULT = "https://api.anthropic.com/v1/messages"


class ClineConfigError(Exception):
    """Raised when Cline proxy install cannot proceed."""


def _get_memor_dir() -> Path:
    return Path.home() / ".memor"


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
    return f"http://127.0.0.1:{port}/cline/v1"


def proxy_anthropic_base_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/cline"


def cline_settings_path() -> Path:
    """Pick Cursor or VS Code User settings where Cline is configured."""
    for app in ("Cursor", "Code"):
        path = vscode_user_settings_path(app)
        if not path.exists():
            continue
        settings = load_settings_json(path)
        if any(str(k).startswith("cline.") for k in settings):
            return path
    return vscode_user_settings_path("Code")


def cline_paths() -> tuple[Path, Path, str]:
    return (
        cline_settings_path(),
        _get_memor_dir() / "proxy-backup-cline.json",
        "{}\n",
    )


def discover_cline_upstream(
    settings_path: Path,
    *,
    port: int | None = None,
    upstream_url: str | None = None,
) -> tuple[str, str, str]:
    if upstream_url and str(upstream_url).strip():
        url = str(upstream_url).strip()
        protocol = "anthropic" if "/messages" in url or "anthropic" in url else "openai"
        normalize = _normalize_anthropic_upstream if protocol == "anthropic" else _normalize_openai_upstream
        return protocol, normalize(url), "custom"

    settings = load_settings_json(settings_path)
    provider = str(settings.get("cline.apiProvider") or "").lower()
    openai_url = first_url_value(settings, CLINE_OPENAI_KEYS)
    anthropic_url = first_url_value(settings, CLINE_ANTHROPIC_KEYS)

    if "anthropic" in provider and anthropic_url and not _is_memor_localhost(anthropic_url, port):
        return "anthropic", _normalize_anthropic_upstream(anthropic_url), "anthropic"
    if openai_url and not _is_memor_localhost(openai_url, port):
        return "openai", _normalize_openai_upstream(openai_url), "openai-compatible"
    if anthropic_url and not _is_memor_localhost(anthropic_url, port):
        return "anthropic", _normalize_anthropic_upstream(anthropic_url), "anthropic"

    return "openai", _OPENAI_DEFAULT, "openai-compatible"


def install_cline_proxy(port: int, *, upstream_url: str | None = None) -> list[str]:
    from memor.proxy.install import backup_agent_config

    config_path = cline_settings_path()
    backup_agent_config("cline")

    protocol, base_url, provider_name = discover_cline_upstream(
        config_path, port=port, upstream_url=upstream_url,
    )
    set_proxy_upstream(
        "cline",
        protocol=protocol,
        base_url=base_url,
        provider_name=provider_name,
    )

    settings = load_settings_json(config_path)
    if protocol == "anthropic":
        updates = {CLINE_ANTHROPIC_KEYS[0]: proxy_anthropic_base_url(port)}
    else:
        updates = {CLINE_OPENAI_KEYS[0]: proxy_openai_base_url(port)}
    settings = set_settings_keys(settings, updates)
    write_settings_json(config_path, settings)
    set_proxy_agent("cline", True)

    return [
        f"Updated Cline settings at {config_path}",
        f"  Provider protocol: {protocol}",
        f"  Base URL → Memor proxy (ledger agent=cline via path prefix)",
        f"  Verify in Cline: Settings → API Provider → Base URL",
        f"  Docs: {CLINE_DOCS}",
    ]


def uninstall_cline_proxy() -> None:
    config_path, backup_path, _ = cline_paths()
    if backup_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(backup_path.read_text())
        backup_path.unlink()
    clear_proxy_upstream("cline")
    set_proxy_agent("cline", False)


def strip_cline_proxy_urls(port: int) -> str:
    config_path = cline_settings_path()
    if not config_path.exists():
        return "cline: no config file to strip"

    settings = load_settings_json(config_path)
    proxy_openai = proxy_openai_base_url(port)
    proxy_anthropic = proxy_anthropic_base_url(port)
    changed = False
    for key in CLINE_OPENAI_KEYS + CLINE_ANTHROPIC_KEYS:
        value = settings.get(key)
        if value in (proxy_openai, proxy_anthropic) or _is_memor_localhost(str(value or ""), port):
            settings.pop(key, None)
            changed = True

    if changed:
        write_settings_json(config_path, settings)
        return "cline: removed Memor base URL keys"
    return "cline: no Memor base URL to strip"
