"""Goose agent proxy install/uninstall helpers."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from memor.config import clear_proxy_upstream, set_proxy_agent, set_proxy_upstream

_OPENAI_DEFAULT_HOST = "https://api.openai.com"
_OPENAI_DEFAULT_BASE_PATH = "v1/chat/completions"
_ANTHROPIC_DEFAULT_HOST = "https://api.anthropic.com"

# Goose Desktop can register custom_* providers in config.yaml without writing
# custom_providers/*.json. Map known provider ids (and model hints) to upstream URLs.
_KNOWN_CUSTOM_UPSTREAMS: dict[str, tuple[str, str]] = {
    "custom_deepseek": (
        "https://api.deepseek.com/v1/chat/completions",
        "DEEPSEEK_API_KEY",
    ),
    "custom_moonshot": (
        "https://api.moonshot.ai/v1/chat/completions",
        "MOONSHOT_API_KEY",
    ),
    "custom_kimi": (
        "https://api.kimi.com/coding/v1/chat/completions",
        "KIMI_API_KEY",
    ),
    "custom_openrouter": (
        "https://openrouter.ai/api/v1/chat/completions",
        "OPENROUTER_API_KEY",
    ),
    "custom_groq": (
        "https://api.groq.com/openai/v1/chat/completions",
        "GROQ_API_KEY",
    ),
}

_MODEL_UPSTREAM_HINTS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"^deepseek", re.I), "https://api.deepseek.com/v1/chat/completions", "DEEPSEEK_API_KEY"),
    (re.compile(r"^moonshot|^kimi", re.I), "https://api.moonshot.ai/v1/chat/completions", "MOONSHOT_API_KEY"),
    (re.compile(r"^gpt-|^o[0-9]", re.I), "https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
    (re.compile(r"^claude", re.I), "https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY"),
)


class GooseProviderNotFoundError(FileNotFoundError):
    """Raised when Goose active_provider has no custom_providers JSON file."""


def _memor_dir() -> Path:
    return Path.home() / ".memor"


def _goose_config_dir() -> Path:
    return Path.home() / ".config" / "goose"


def _goose_config_path() -> Path:
    return _goose_config_dir() / "config.yaml"


def _custom_providers_dir() -> Path:
    return _goose_config_dir() / "custom_providers"


def _is_memor_localhost(url: str, port: int | None = None) -> bool:
    if "127.0.0.1" not in url and "localhost" not in url:
        return False
    if port is None:
        return True
    return f":{port}" in url or f":{port}/" in url


def _read_yaml_scalar(text: str, key: str) -> str | None:
    """Read a top-level scalar key from minimal YAML text."""
    match = re.search(
        rf"^(?:\s*){re.escape(key)}\s*:\s*(.+?)\s*$",
        text,
        re.MULTILINE,
    )
    if not match:
        return None
    value = match.group(1).strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def _set_yaml_scalar(text: str, key: str, value: str) -> str:
    """Set or insert a top-level scalar key in minimal YAML text."""
    line = f'{key}: "{value}"'
    pattern = re.compile(rf"^(?:\s*){re.escape(key)}\s*:\s*.+?\s*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(line, text, count=1)
    body = text.rstrip("\n")
    if body:
        return body + "\n" + line + "\n"
    return line + "\n"


def _engine_to_protocol(engine: str) -> str:
    engine = engine.strip().lower()
    if engine == "anthropic":
        return "anthropic"
    if engine == "openai":
        return "openai"
    raise ValueError(f"Unsupported Goose provider engine: {engine!r}")


def _normalize_openai_upstream(host: str, base_path: str) -> str:
    host = host.rstrip("/")
    base_path = base_path.strip("/")
    if base_path.endswith("chat/completions"):
        return f"{host}/{base_path}"
    if base_path.endswith("v1"):
        return f"{host}/{base_path}/chat/completions"
    return f"{host}/{base_path}"


def _normalize_anthropic_upstream(host: str) -> str:
    host = host.rstrip("/")
    if host.endswith("/v1/messages"):
        return host
    if host.endswith("/v1"):
        return f"{host}/messages"
    return f"{host}/v1/messages"


def _proxy_base_url(port: int, protocol: str) -> str:
    if protocol == "anthropic":
        return f"http://127.0.0.1:{port}/v1/messages"
    return f"http://127.0.0.1:{port}/v1/chat/completions"


def _custom_provider_path(provider_name: str) -> Path:
    return _custom_providers_dir() / f"{provider_name}.json"


def _custom_provider_backup_path(provider_name: str) -> Path:
    return _memor_dir() / f"proxy-backup-goose-{provider_name}.json"


def _read_active_provider() -> str:
    config_path = _goose_config_path()
    text = config_path.read_text() if config_path.exists() else ""
    provider = (_read_yaml_scalar(text, "active_provider") or "").strip()
    if not provider:
        raise ValueError(
            "Goose config missing active_provider. Set a provider in Goose "
            "Settings → Models, then re-run memor install-proxy --agent goose."
        )
    return provider


def _read_yaml_provider_model(text: str, provider_name: str) -> str | None:
    """Read model name from a providers.<name> block in config.yaml."""
    in_block = False
    for line in text.splitlines():
        if re.match(rf"^\s{{2}}{re.escape(provider_name)}:\s*$", line):
            in_block = True
            continue
        if in_block:
            if line and not line.startswith(" "):
                break
            if re.match(r"^\s{2}\S", line) and not line.startswith("    "):
                break
            match = re.match(r"^\s+model:\s*(.+)$", line)
            if match:
                value = match.group(1).strip()
                if value.startswith('"') and value.endswith('"'):
                    return value[1:-1]
                if value.startswith("'") and value.endswith("'"):
                    return value[1:-1]
                return value
    return None


def _protocol_from_upstream_url(base_url: str) -> str:
    path = base_url.rstrip("/").split("/")[-2:] if "/" in base_url else []
    if base_url.rstrip("/").endswith("/messages") or "/v1/messages" in base_url:
        return "anthropic"
    return "openai"


def _materialize_custom_provider_json(
    provider_name: str,
    base_url: str,
    *,
    config_text: str = "",
    api_key_env: str | None = None,
) -> Path:
    """Create custom_providers JSON when Goose Desktop registered the provider in yaml only."""
    protocol = _protocol_from_upstream_url(base_url)
    engine = "anthropic" if protocol == "anthropic" else "openai"
    model = _read_yaml_provider_model(config_text, provider_name) or "default"
    if api_key_env is None:
        inferred = _infer_custom_upstream(provider_name, model if model != "default" else None)
        api_key_env = inferred[1] if inferred else f"{provider_name.upper()}_API_KEY"
    provider_path = _custom_provider_path(provider_name)
    provider_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "name": provider_name,
        "engine": engine,
        "display_name": provider_name.replace("custom_", "").replace("_", " ").title(),
        "description": f"Materialized by memor install-proxy for {provider_name}",
        "api_key_env": api_key_env,
        "base_url": base_url.rstrip("/"),
        "models": [{"name": model, "context_limit": 128000}],
        "requires_auth": True,
        "supports_streaming": True,
    }
    provider_path.write_text(json.dumps(data, indent=2) + "\n")
    return provider_path


def _resolve_upstream_url_override() -> str | None:
    return (os.environ.get("MEMOR_GOOSE_UPSTREAM_URL") or "").strip() or None


def _infer_custom_upstream(
    provider_name: str,
    model: str | None,
) -> tuple[str, str] | None:
    """Guess upstream URL + api_key_env for Desktop-only custom providers."""
    if provider_name in _KNOWN_CUSTOM_UPSTREAMS:
        return _KNOWN_CUSTOM_UPSTREAMS[provider_name]
    slug = provider_name.removeprefix("custom_")
    for key, value in _KNOWN_CUSTOM_UPSTREAMS.items():
        if key.removeprefix("custom_") == slug:
            return value
    if model:
        for pattern, base_url, api_key_env in _MODEL_UPSTREAM_HINTS:
            if pattern.search(model):
                return (base_url, api_key_env)
    return None


def discover_goose_upstream(
    port: int | None = None,
    *,
    upstream_url: str | None = None,
) -> tuple[str, str, str, str]:
    """Return (protocol, base_url, provider_name, rewrite_kind) from Goose config."""
    provider_name = _read_active_provider()

    if provider_name.startswith("custom_"):
        provider_path = _custom_provider_path(provider_name)
        override = (upstream_url or _resolve_upstream_url_override() or "").strip()
        config_text = (
            _goose_config_path().read_text() if _goose_config_path().exists() else ""
        )
        if not provider_path.exists():
            materialize_url = override
            materialize_api_key_env: str | None = None
            if not materialize_url:
                model = _read_yaml_provider_model(config_text, provider_name)
                inferred = _infer_custom_upstream(provider_name, model)
                if inferred:
                    materialize_url, materialize_api_key_env = inferred
            if materialize_url:
                _materialize_custom_provider_json(
                    provider_name,
                    materialize_url,
                    config_text=config_text,
                    api_key_env=materialize_api_key_env,
                )
            else:
                hint = (
                    "Goose Desktop often stores custom providers in config.yaml without "
                    "writing custom_providers/*.json. Re-save the provider in Goose → "
                    "Settings → Models, or pass the upstream URL:\n\n"
                    "  memor install-proxy --agent goose "
                    "--upstream-url 'https://api.deepseek.com/v1/chat/completions'\n\n"
                    "Or set MEMOR_GOOSE_UPSTREAM_URL for the same value."
                )
                raise GooseProviderNotFoundError(
                    f"Goose active provider {provider_name!r} has no config file at "
                    f"{provider_path}. {hint}"
                )
        data = json.loads(provider_path.read_text() or "{}")
        engine = str(data.get("engine", "openai"))
        protocol = _engine_to_protocol(engine)
        base_url = str(data.get("base_url", "")).strip()
        if not base_url:
            raise ValueError(
                f"Goose custom provider {provider_name!r} is missing base_url in "
                f"{provider_path}."
            )
        if port is not None and _is_memor_localhost(base_url, port):
            if protocol == "anthropic":
                base_url = "https://api.anthropic.com/v1/messages"
            else:
                base_url = "https://api.openai.com/v1/chat/completions"
        return (protocol, base_url, provider_name, "custom_json")

    config_text = (
        _goose_config_path().read_text() if _goose_config_path().exists() else ""
    )

    if provider_name == "anthropic":
        host = (
            os.environ.get("ANTHROPIC_HOST")
            or _read_yaml_scalar(config_text, "ANTHROPIC_HOST")
            or _ANTHROPIC_DEFAULT_HOST
        ).strip()
        base_url = _normalize_anthropic_upstream(host)
        if port is not None and _is_memor_localhost(base_url, port):
            base_url = "https://api.anthropic.com/v1/messages"
        return ("anthropic", base_url, provider_name, "anthropic_builtin")

    host = (
        os.environ.get("OPENAI_HOST")
        or _read_yaml_scalar(config_text, "OPENAI_HOST")
        or _OPENAI_DEFAULT_HOST
    ).strip()
    base_path = (
        os.environ.get("OPENAI_BASE_PATH")
        or _read_yaml_scalar(config_text, "OPENAI_BASE_PATH")
        or _OPENAI_DEFAULT_BASE_PATH
    ).strip()
    base_url = _normalize_openai_upstream(host, base_path)
    if port is not None and _is_memor_localhost(base_url, port):
        base_url = "https://api.openai.com/v1/chat/completions"
    return ("openai", base_url, provider_name, "openai_builtin")


def _goose_paths() -> tuple[Path, Path, str]:
    memor_dir = _memor_dir()
    return (
        _goose_config_path(),
        memor_dir / "proxy-backup-goose.yaml",
        "active_provider: openai\n",
    )


def _backup_custom_provider(provider_name: str) -> Path:
    """Back up a custom provider JSON; leave existing backup untouched."""
    memor_dir = _memor_dir()
    memor_dir.mkdir(parents=True, exist_ok=True)
    backup_path = _custom_provider_backup_path(provider_name)
    if backup_path.exists():
        return backup_path
    provider_path = _custom_provider_path(provider_name)
    backup_path.write_text(provider_path.read_text() if provider_path.exists() else "{}")
    return backup_path


def install_goose_proxy(port: int, *, upstream_url: str | None = None) -> None:
    """Install Goose proxy by rewriting provider config to point at Memor."""
    from memor.proxy.install import backup_agent_config

    protocol, base_url, provider_name, rewrite_kind = discover_goose_upstream(
        port,
        upstream_url=upstream_url,
    )

    backup_agent_config("goose")

    if rewrite_kind == "custom_json":
        _backup_custom_provider(provider_name)
        provider_path = _custom_provider_path(provider_name)
        data = json.loads(provider_path.read_text() or "{}")
        data["base_url"] = _proxy_base_url(port, protocol)
        headers = dict(data.get("headers") or {})
        headers["x-agent"] = "goose"
        data["headers"] = headers
        provider_path.parent.mkdir(parents=True, exist_ok=True)
        provider_path.write_text(json.dumps(data, indent=2) + "\n")
    elif rewrite_kind == "openai_builtin":
        config_path = _goose_config_path()
        text = config_path.read_text() if config_path.exists() else ""
        text = _set_yaml_scalar(text, "OPENAI_HOST", f"http://127.0.0.1:{port}")
        text = _set_yaml_scalar(text, "OPENAI_BASE_PATH", "v1/chat/completions")
        text = _set_yaml_scalar(text, "OPENAI_CUSTOM_HEADERS", '{"x-agent":"goose"}')
        _goose_config_dir().mkdir(parents=True, exist_ok=True)
        config_path.write_text(text)
    elif rewrite_kind == "anthropic_builtin":
        config_path = _goose_config_path()
        text = config_path.read_text() if config_path.exists() else ""
        text = _set_yaml_scalar(text, "ANTHROPIC_HOST", f"http://127.0.0.1:{port}")
        _goose_config_dir().mkdir(parents=True, exist_ok=True)
        config_path.write_text(text)

    set_proxy_upstream(
        "goose",
        protocol=protocol,
        base_url=base_url,
        provider_name=provider_name,
    )
    set_proxy_agent("goose", True)


def uninstall_goose_proxy() -> None:
    """Restore Goose config from backups and clear Memor proxy flags."""
    config_path, backup_path, _ = _goose_paths()
    if backup_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(backup_path.read_text())
        backup_path.unlink()

    provider_name = None
    try:
        provider_name = _read_active_provider()
    except ValueError:
        pass

    if provider_name and provider_name.startswith("custom_"):
        json_backup = _custom_provider_backup_path(provider_name)
        provider_path = _custom_provider_path(provider_name)
        if json_backup.exists():
            provider_path.parent.mkdir(parents=True, exist_ok=True)
            provider_path.write_text(json_backup.read_text())
            json_backup.unlink()

    clear_proxy_upstream("goose")
    set_proxy_agent("goose", False)


def strip_goose_proxy_urls(port: int) -> str:
    """Best-effort remove Memor localhost URLs when backup is missing."""
    provider_name = None
    try:
        provider_name = _read_active_provider()
    except ValueError:
        return "goose: no active_provider to strip"

    if provider_name and provider_name.startswith("custom_"):
        provider_path = _custom_provider_path(provider_name)
        if not provider_path.exists():
            return f"goose: no custom provider file at {provider_path}"
        data = json.loads(provider_path.read_text() or "{}")
        base_url = str(data.get("base_url", ""))
        headers = dict(data.get("headers") or {})
        changed = False
        if _is_memor_localhost(base_url, port):
            data.pop("base_url", None)
            changed = True
        if headers.get("x-agent") == "goose":
            headers.pop("x-agent", None)
            if headers:
                data["headers"] = headers
            else:
                data.pop("headers", None)
            changed = True
        if changed:
            provider_path.write_text(json.dumps(data, indent=2) + "\n")
            return f"goose: stripped Memor URLs from {provider_name}"
        return f"goose: no Memor URLs in {provider_name}"

    config_path = _goose_config_path()
    if not config_path.exists():
        return "goose: no config file to strip"
    text = config_path.read_text()
    original = text
    for key in ("OPENAI_HOST", "ANTHROPIC_HOST", "OPENAI_BASE_PATH", "OPENAI_CUSTOM_HEADERS"):
        value = _read_yaml_scalar(text, key) or ""
        if _is_memor_localhost(value, port) or (
            key == "OPENAI_CUSTOM_HEADERS" and "goose" in value
        ):
            text = re.sub(rf"^{re.escape(key)}\s*:\s*.+?\n", "", text, flags=re.MULTILINE)
    if text != original:
        config_path.write_text(text)
        return "goose: removed Memor proxy keys from config.yaml"
    return "goose: no Memor proxy keys to strip"
