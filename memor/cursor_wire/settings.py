"""Cursor settings.json keys for subscription wire MITM."""
from __future__ import annotations

from pathlib import Path

from memor.proxy.cursor_install import cursor_paths
from memor.proxy.vscode_settings import (
    load_settings_json,
    remove_settings_keys,
    set_settings_keys,
    write_settings_json,
)

WIRE_PROXY_KEYS = (
    "http.proxy",
    "http.proxySupport",
    "http.proxyStrictSSL",
    "http.noProxy",
    "http.proxyBypassList",
    "cursor.general.disableHttp2",
)

_NOPROXY = "127.0.0.1,localhost,::1"


def memor_wire_proxy_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def is_memor_wire_proxy(url: str | None) -> bool:
    if not url:
        return False
    u = str(url).strip().lower()
    return u.startswith("http://127.0.0.1:") or u.startswith("http://localhost:")


def existing_foreign_proxy(settings: dict) -> str | None:
    """Return http.proxy if set and not a Memor wire URL."""
    value = settings.get("http.proxy")
    if value is None or not str(value).strip():
        return None
    if is_memor_wire_proxy(str(value)):
        return None
    return str(value).strip()


def wire_settings_updates(port: int) -> dict:
    return {
        "http.proxy": memor_wire_proxy_url(port),
        "http.proxySupport": "override",
        "http.proxyStrictSSL": False,
        "http.noProxy": _NOPROXY,
        "http.proxyBypassList": _NOPROXY,
        "cursor.general.disableHttp2": True,
    }


def write_cursor_wire_settings(port: int) -> Path:
    config_path, _, _ = cursor_paths()
    settings = load_settings_json(config_path)
    settings = set_settings_keys(settings, wire_settings_updates(port))
    write_settings_json(config_path, settings)
    return config_path


def strip_cursor_wire_settings() -> str:
    """Remove Memor wire proxy keys (leave BYOK base URLs alone).

    Also rewrites settings.json to drop commented-out wire keys (JSONC), which
    users often leave after manually disabling a broken MITM.
    """
    config_path, _, _ = cursor_paths()
    if not config_path.exists():
        return "cursor wire: no settings file"
    raw = config_path.read_text(encoding="utf-8", errors="replace")
    settings = load_settings_json(config_path)
    proxy = settings.get("http.proxy")
    should_strip = is_memor_wire_proxy(str(proxy) if proxy is not None else None)
    compact = raw.replace(" ", "")
    commented_wire = any(
        f'//"{key}"' in compact or f'//\n"{key}"' in compact for key in WIRE_PROXY_KEYS
    ) or '//"http.proxy"' in compact
    if not should_strip and settings.get("cursor.general.disableHttp2") is not True:
        if not any(k in settings for k in ("http.noProxy", "http.proxyBypassList")):
            if not commented_wire:
                return "cursor wire: no Memor wire proxy keys"
    settings, changed = remove_settings_keys(settings, WIRE_PROXY_KEYS)
    # Always rewrite when commented wire keys remain so Cursor gets valid clean JSON.
    if changed or commented_wire:
        write_settings_json(config_path, settings)
        return "cursor wire: removed http.proxy / disableHttp2 keys"
    return "cursor wire: no Memor wire proxy keys"


def failover_cursor_wire(reason: str) -> list[str]:
    """Strip wire keys and clear cursor_wire flag (BYOK may remain)."""
    from memor.config import set_cursor_wire

    lines = [f"cursor-wire failover: {reason}"]
    lines.append(strip_cursor_wire_settings())
    set_cursor_wire(False)
    lines.append("cursor_wire flag cleared")
    return lines
