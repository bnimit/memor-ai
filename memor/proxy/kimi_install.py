"""Kimi CLI proxy install/uninstall helpers."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from memor.cli import kimi_config_path
from memor.config import clear_proxy_upstream, set_proxy_agent, set_proxy_upstream

KIMI_VSCODE_DOCS = (
    "https://www.kimi.com/code/docs/en/kimi-code-for-vscode/getting-started"
)

_OPENAI_TYPES = frozenset({"kimi", "openai", "openai_responses"})
_ANTHROPIC_TYPES = frozenset({"anthropic"})


def _is_memor_localhost(url: str, port: int | None = None) -> bool:
    if "127.0.0.1" not in url and "localhost" not in url:
        return False
    if port is None:
        return True
    return f":{port}" in url or f":{port}/" in url


class KimiConfigError(Exception):
    """Raised when Kimi config.toml is missing required proxy install fields."""


def _get_memor_dir() -> Path:
    return Path.home() / ".memor"


def kimi_paths() -> tuple[Path, Path, str]:
    """Return (config path, proxy backup path, empty-config placeholder)."""
    return (
        kimi_config_path(),
        _get_memor_dir() / "proxy-backup-kimi.toml",
        "",
    )


def _escape_provider_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return f'"{key}"'


def _provider_header_candidates(provider_key: str) -> set[str]:
    escaped = _escape_provider_key(provider_key)
    headers = {f"[providers.{escaped}]"}
    headers.add(f'[providers."{provider_key}"]')
    if re.fullmatch(r"[A-Za-z0-9_-]+", provider_key):
        headers.add(f"[providers.{provider_key}]")
    return headers


def _provider_table_header(provider_key: str) -> str:
    return f"[providers.{_escape_provider_key(provider_key)}]"


def _provider_section_prefixes(provider_key: str) -> list[str]:
    escaped = _escape_provider_key(provider_key)
    prefixes = [f"[providers.{escaped}"]
    prefixes.append(f'[providers."{provider_key}"')
    if re.fullmatch(r"[A-Za-z0-9_-]+", provider_key):
        prefixes.append(f"[providers.{provider_key}")
    return prefixes


def _is_provider_header(line: str, provider_key: str) -> bool:
    return line.strip() in _provider_header_candidates(provider_key)


def _load_config_dict(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    text = config_path.read_text()
    if not text.strip():
        return {}
    return tomllib.loads(text)


def _resolve_active_provider(config: dict) -> tuple[str, dict]:
    providers = config.get("providers") or {}
    if not providers:
        raise KimiConfigError(
            "No [providers] section found in Kimi config.toml. "
            f"Configure a provider with base_url, then re-run install-proxy. See {KIMI_VSCODE_DOCS}"
        )

    default_model = str(config.get("default_model") or "").strip()
    if not default_model:
        provider_key = next(iter(providers))
        return provider_key, providers[provider_key]

    models = config.get("models") or {}
    model = models.get(default_model)
    if not isinstance(model, dict):
        raise KimiConfigError(
            f"default_model {default_model!r} is not defined under [models]. "
            f"Fix ~/.kimi/config.toml, then re-run install-proxy. See {KIMI_VSCODE_DOCS}"
        )

    provider_key = str(model.get("provider") or "").strip()
    if not provider_key:
        raise KimiConfigError(
            f"Model {default_model!r} has no provider. "
            f"Set provider under [models], then re-run install-proxy. See {KIMI_VSCODE_DOCS}"
        )
    provider = providers.get(provider_key)
    if not isinstance(provider, dict):
        raise KimiConfigError(
            f"Provider {provider_key!r} is not defined under [providers]. "
            f"Fix ~/.kimi/config.toml, then re-run install-proxy. See {KIMI_VSCODE_DOCS}"
        )
    return provider_key, provider


def _provider_type(provider: dict) -> str:
    return str(provider.get("type") or "").strip().lower()


def _protocol_for_type(provider_type: str) -> str:
    if provider_type in _ANTHROPIC_TYPES:
        return "anthropic"
    return "openai"


def _provider_base_url(provider: dict, provider_type: str) -> str | None:
    base_url = provider.get("base_url")
    if base_url is not None and str(base_url).strip():
        return str(base_url).strip()

    env = provider.get("env") or {}
    if not isinstance(env, dict):
        return None

    if provider_type in _ANTHROPIC_TYPES:
        value = env.get("ANTHROPIC_BASE_URL")
        return str(value).strip() if value else None

    for key in ("KIMI_BASE_URL", "OPENAI_BASE_URL"):
        value = env.get(key)
        if value:
            return str(value).strip()
    return None


def _normalize_upstream(url: str, protocol: str) -> str:
    url = url.rstrip("/")
    if protocol == "anthropic":
        if url.endswith("/v1/messages"):
            return url
        if url.endswith("/v1"):
            return f"{url}/messages"
        return f"{url}/v1/messages"

    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/v1/chat/completions"


def _proxy_base_url(port: int, protocol: str) -> str:
    if protocol == "anthropic":
        return f"http://127.0.0.1:{port}"
    return f"http://127.0.0.1:{port}/v1"


def discover_kimi_upstream(config_path: Path, *, port: int | None = None) -> tuple[str, str, str]:
    """Return (protocol, normalized upstream URL, provider key) for the active provider."""
    config = _load_config_dict(config_path)
    provider_key, provider = _resolve_active_provider(config)
    provider_type = _provider_type(provider)
    protocol = _protocol_for_type(provider_type)

    base_url = _provider_base_url(provider, provider_type)
    if not base_url:
        raise KimiConfigError(
            "Kimi proxy install requires a writable base_url in config.toml "
            "(API-key / configurable provider mode). Account-login-only setups "
            f"have no base_url to rewrite. See {KIMI_VSCODE_DOCS}"
        )

    if port is not None and _is_memor_localhost(base_url, port):
        raise KimiConfigError(
            "Kimi config already points at the Memor proxy. "
            "Run memor uninstall-proxy --agent kimi before reinstalling."
        )

    return protocol, _normalize_upstream(base_url, protocol), provider_key


def _section_bounds(lines: list[str], provider_key: str) -> tuple[int, int] | None:
    start = next(
        (i for i, line in enumerate(lines) if _is_provider_header(line, provider_key)),
        None,
    )
    if start is None:
        return None

    prefixes = _provider_section_prefixes(provider_key)
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped.startswith("["):
            continue
        if any(stripped.startswith(f"{prefix}.") for prefix in prefixes):
            continue
        if _is_provider_header(stripped, provider_key):
            continue
        end = i
        break
    return start, end


def _set_scalar_in_section(
    lines: list[str],
    start: int,
    end: int,
    key: str,
    value: str,
) -> None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    value_line = f'{key} = "{value}"'
    for i in range(start + 1, end):
        if pattern.match(lines[i]):
            lines[i] = value_line
            return
    lines.insert(start + 1, value_line)


def _merge_custom_headers_in_section(
    lines: list[str],
    start: int,
    end: int,
    agent: str,
) -> None:
    inline = re.compile(
        r'^(\s*custom_headers\s*=\s*\{)(.*)(\}\s*)$',
        re.DOTALL,
    )
    for i in range(start + 1, end):
        match = inline.match(lines[i])
        if not match:
            continue
        inner = match.group(2).strip()
        if re.search(r'["\']?x-agent["\']?\s*=', inner, re.IGNORECASE):
            inner = re.sub(
                r'(["\']?x-agent["\']?\s*=\s*["\'])[^"\']*(["\'])',
                rf'\1{agent}\2',
                inner,
                count=1,
                flags=re.IGNORECASE,
            )
        elif inner:
            if not inner.endswith(","):
                inner = f"{inner},"
            inner = f'{inner} "x-agent" = "{agent}"'
        else:
            inner = f' "x-agent" = "{agent}"'
        lines[i] = f"{match.group(1)}{inner}{match.group(3)}"
        return

    lines.insert(start + 2, f'custom_headers = {{ "x-agent" = "{agent}" }}')


def _rewrite_kimi_provider(
    text: str,
    provider_key: str,
    *,
    port: int,
    protocol: str,
) -> str:
    lines = text.splitlines()
    bounds = _section_bounds(lines, provider_key)
    if bounds is None:
        header = _provider_table_header(provider_key)
        block = [
            header,
            f'type = "{protocol if protocol != "openai" else "kimi"}"',
            f'base_url = "{_proxy_base_url(port, protocol)}"',
            'custom_headers = { "x-agent" = "kimi" }',
        ]
        if text.strip():
            return text.rstrip("\n") + "\n\n" + "\n".join(block) + "\n"
        return "\n".join(block) + "\n"

    start, end = bounds
    _set_scalar_in_section(
        lines,
        start,
        end,
        "base_url",
        _proxy_base_url(port, protocol),
    )
    end = _section_bounds(lines, provider_key)[1]
    _merge_custom_headers_in_section(lines, start, end, "kimi")
    return "\n".join(lines).rstrip("\n") + "\n"


def install_kimi_proxy(port: int) -> None:
    """Install Kimi proxy by rewriting the active provider in config.toml."""
    from memor.proxy.install import backup_agent_config

    config_path = kimi_config_path()
    backup_agent_config("kimi")

    config_text = config_path.read_text() if config_path.exists() else ""
    protocol, base_url, provider_key = discover_kimi_upstream(config_path, port=port)

    set_proxy_upstream(
        "kimi",
        protocol=protocol,
        base_url=base_url,
        provider_name=provider_key,
    )

    config_text = _rewrite_kimi_provider(
        config_text,
        provider_key,
        port=port,
        protocol=protocol,
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text)
    set_proxy_agent("kimi", True)


def uninstall_kimi_proxy() -> None:
    """Restore Kimi config from backup and clear proxy state."""
    config_path, backup_path, _ = kimi_paths()
    if backup_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(backup_path.read_text())
        backup_path.unlink()
    clear_proxy_upstream("kimi")
    set_proxy_agent("kimi", False)


def strip_kimi_proxy_urls(port: int) -> str:
    """Best-effort remove Memor localhost base_url and x-agent header."""
    config_path = kimi_config_path()
    if not config_path.exists():
        return "kimi: no config file to strip"

    text = config_path.read_text()
    if not text.strip():
        return "kimi: no config content to strip"

    try:
        config = tomllib.loads(text)
        provider_key, _ = _resolve_active_provider(config)
    except (KimiConfigError, tomllib.TOMLDecodeError):
        return "kimi: could not resolve active provider to strip"

    lines = text.splitlines()
    bounds = _section_bounds(lines, provider_key)
    if bounds is None:
        return "kimi: active provider section not found"

    start, end = bounds
    changed = False
    base_pattern = re.compile(r"^\s*base_url\s*=")
    header_pattern = re.compile(r"^\s*custom_headers\s*=")
    new_lines: list[str] = []

    for i, line in enumerate(lines):
        if start <= i < end and base_pattern.match(line):
            if _is_memor_localhost(line, port):
                changed = True
                continue
        if start <= i < end and header_pattern.match(line):
            updated = _strip_x_agent_from_headers_line(line)
            if updated is None:
                changed = True
                continue
            if updated != line:
                changed = True
            line = updated
        new_lines.append(line)

    if changed:
        config_path.write_text("\n".join(new_lines).rstrip("\n") + "\n")
        return "kimi: removed proxy base_url and/or x-agent header"
    return "kimi: no Memor base_url to strip"


def _strip_x_agent_from_headers_line(line: str) -> str | None:
    inline = re.match(
        r'^(\s*custom_headers\s*=\s*\{)(.*)(\}\s*)$',
        line,
        re.DOTALL,
    )
    if not inline:
        return line

    inner = inline.group(2)
    cleaned = re.sub(
        r',?\s*["\']?x-agent["\']?\s*=\s*"[^"]*"\s*,?',
        "",
        inner,
        count=1,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.strip().strip(",").strip()
    if not cleaned:
        return None
    return f"{inline.group(1)}{cleaned}{inline.group(3)}"

