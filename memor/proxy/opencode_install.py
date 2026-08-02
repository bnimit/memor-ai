"""OpenCode CLI proxy install helpers."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from memor.config import clear_proxy_upstream, set_proxy_agent, set_proxy_upstream

OPENCODE_DOCS = "https://opencode.ai/docs/providers"

_OPENAI_DEFAULT = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_DEFAULT = "https://api.anthropic.com/v1/messages"
_BUILTIN_OPENAI = frozenset({"openai"})
_BUILTIN_ANTHROPIC = frozenset({"anthropic"})


class OpenCodeConfigError(Exception):
    """Raised when OpenCode proxy install cannot proceed."""


def _get_memor_dir() -> Path:
    return Path.home() / ".memor"


def opencode_config_path() -> Path:
    env_path = os.environ.get("OPENCODE_CONFIG", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    base = Path.home() / ".config" / "opencode"
    jsonc = base / "opencode.jsonc"
    if jsonc.exists():
        return jsonc
    return base / "opencode.json"


def opencode_paths() -> tuple[Path, Path, str]:
    return (
        opencode_config_path(),
        _get_memor_dir() / "proxy-backup-opencode.json",
        "{}\n",
    )


def _is_memor_localhost(url: str, port: int | None = None) -> bool:
    if "127.0.0.1" not in url and "localhost" not in url:
        return False
    if port is None:
        return True
    return f":{port}" in url or f":{port}/" in url


def _parse_json_loose(text: str) -> dict:
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        cleaned = re.sub(r"^\s*//[^\n]*\n", "", text, flags=re.MULTILINE)
        cleaned = re.sub(r",\s*//[^\n]*", ",", cleaned)
        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}


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


def _protocol_for_provider(provider_id: str, provider: dict) -> str:
    npm = str(provider.get("npm") or "").lower()
    if provider_id in _BUILTIN_ANTHROPIC or "anthropic" in npm:
        return "anthropic"
    return "openai"


def _provider_base_url(provider: dict) -> str | None:
    options = provider.get("options") or {}
    if not isinstance(options, dict):
        return None
    for key in ("baseURL", "baseUrl", "base_url"):
        value = options.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    api = provider.get("api")
    if isinstance(api, dict):
        for key in ("baseURL", "baseUrl", "base_url"):
            value = api.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _active_provider_id(config: dict) -> str:
    model = str(config.get("model") or "").strip()
    if "/" in model:
        return model.split("/", 1)[0]
    provider = config.get("provider")
    if isinstance(provider, dict) and provider:
        return next(iter(provider))
    if isinstance(provider, str) and provider.strip():
        return provider.strip()
    raise OpenCodeConfigError(
        "OpenCode config has no `model` (provider/model) or provider block. "
        f"Set a model in {opencode_config_path()}, then re-run install-proxy."
    )


def discover_opencode_upstream(
    config_path: Path,
    *,
    port: int | None = None,
    upstream_url: str | None = None,
) -> tuple[str, str, str, str]:
    """Return (protocol, normalized upstream URL, provider_id, rewrite_kind)."""
    if upstream_url and str(upstream_url).strip():
        url = str(upstream_url).strip()
        protocol = "anthropic" if "/messages" in url or "anthropic" in url else "openai"
        normalize = _normalize_anthropic_upstream if protocol == "anthropic" else _normalize_openai_upstream
        return protocol, normalize(url), "custom", "upstream_flag"

    config = _parse_json_loose(config_path.read_text() if config_path.exists() else "")
    provider_id = _active_provider_id(config)
    providers = config.get("provider") or {}
    if not isinstance(providers, dict):
        providers = {}
    provider = providers.get(provider_id)
    if not isinstance(provider, dict):
        raise OpenCodeConfigError(
            f"Provider {provider_id!r} not found under provider in {config_path}. "
            f"See {OPENCODE_DOCS}"
        )

    protocol = _protocol_for_provider(provider_id, provider)
    base_url = _provider_base_url(provider)
    if not base_url:
        raise OpenCodeConfigError(
            f"Provider {provider_id!r} has no options.baseURL to rewrite. "
            f"Use --upstream-url or configure baseURL first. See {OPENCODE_DOCS}"
        )
    if port is not None and _is_memor_localhost(base_url, port):
        raise OpenCodeConfigError(
            "OpenCode already points at the Memor proxy. "
            "Run memor uninstall-proxy --agent opencode before reinstalling."
        )

    normalize = _normalize_anthropic_upstream if protocol == "anthropic" else _normalize_openai_upstream
    return protocol, normalize(base_url), provider_id, "provider_options"


def _proxy_base_url(port: int, protocol: str) -> str:
    if protocol == "anthropic":
        return f"http://127.0.0.1:{port}/opencode"
    return f"http://127.0.0.1:{port}/opencode/v1"


def _merge_headers(options: dict, agent: str) -> dict:
    headers = dict(options.get("headers") or {})
    if not isinstance(headers, dict):
        headers = {}
    headers["x-agent"] = agent
    options["headers"] = headers
    return options


def install_opencode_proxy(port: int, *, upstream_url: str | None = None) -> None:
    from memor.proxy.install import backup_agent_config

    config_path = opencode_config_path()
    backup_agent_config("opencode")

    protocol, base_url, provider_id, _ = discover_opencode_upstream(
        config_path, port=port, upstream_url=upstream_url,
    )
    set_proxy_upstream(
        "opencode",
        protocol=protocol,
        base_url=base_url,
        provider_name=provider_id,
    )

    text = config_path.read_text() if config_path.exists() else "{}"
    config = _parse_json_loose(text)
    providers = dict(config.get("provider") or {})
    provider = dict(providers.get(provider_id) or {})
    options = dict(provider.get("options") or {})
    options["baseURL"] = _proxy_base_url(port, protocol)
    options = _merge_headers(options, "opencode")
    provider["options"] = options
    providers[provider_id] = provider
    config["provider"] = providers

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    set_proxy_agent("opencode", True)


def uninstall_opencode_proxy() -> None:
    config_path, backup_path, _ = opencode_paths()
    if backup_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(backup_path.read_text())
        backup_path.unlink()
    clear_proxy_upstream("opencode")
    set_proxy_agent("opencode", False)


def strip_opencode_proxy_urls(port: int) -> str:
    config_path = opencode_config_path()
    if not config_path.exists():
        return "opencode: no config file to strip"

    config = _parse_json_loose(config_path.read_text())
    providers = config.get("provider") or {}
    if not isinstance(providers, dict):
        return "opencode: no provider block to strip"

    changed = False
    for provider_id, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        options = provider.get("options")
        if not isinstance(options, dict):
            continue
        base = str(options.get("baseURL") or options.get("baseUrl") or "")
        if not _is_memor_localhost(base, port):
            continue
        options.pop("baseURL", None)
        options.pop("baseUrl", None)
        headers = options.get("headers")
        if isinstance(headers, dict) and headers.get("x-agent") == "opencode":
            headers.pop("x-agent", None)
            if not headers:
                options.pop("headers", None)
        provider["options"] = options
        providers[provider_id] = provider
        changed = True

    if changed:
        config["provider"] = providers
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        return "opencode: removed Memor baseURL / x-agent"
    return "opencode: no Memor baseURL to strip"
