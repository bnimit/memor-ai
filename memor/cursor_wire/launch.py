"""Helpers to launch mitmproxy with the Memor Cursor wire addon."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def addon_path() -> Path:
    return Path(__file__).resolve().parent / "mitm_addon.py"


def mitmproxy_importable() -> bool:
    """True if `mitmproxy` is importable in the current interpreter (pipx venv)."""
    try:
        import mitmproxy  # noqa: F401
        return True
    except ImportError:
        return False


def find_mitm_bin() -> str | None:
    """System mitmdump on PATH (Homebrew standalone). Cannot load memor addons."""
    for name in ("mitmdump", "mitmweb"):
        path = shutil.which(name)
        if path:
            return path
    return None


def os_access_executable(path: Path) -> bool:
    import os

    return os.access(path, os.X_OK)


def venv_mitmdump() -> str | None:
    """mitmdump console script in this env (same site-packages as memor).

    Do not use ``Path(sys.executable).resolve()`` — on macOS Homebrew that
    resolves into the framework ``Python.app`` bundle, not the venv ``bin/``.
    """
    candidates: list[Path] = []
    try:
        import sysconfig

        scripts = sysconfig.get_path("scripts")
        if scripts:
            candidates.append(Path(scripts) / "mitmdump")
    except Exception:
        pass
    candidates.append(Path(sys.prefix) / "bin" / "mitmdump")
    # Unresolved executable parent (venv/bin/python before .resolve())
    candidates.append(Path(sys.executable).parent / "mitmdump")
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file() and os_access_executable(candidate):
            return key
    return None


def mitmproxy_available() -> bool:
    """True if we can run the wire addon (requires mitmproxy in memor's env).

    Homebrew's standalone mitmdump is NOT enough — it ships its own Python and
    cannot ``import memor``. Console script is preferred; ``python -c`` works
    as a fallback when the package is importable.
    """
    return mitmproxy_importable()


def _pipx_package_name() -> str:
    return "memor-cli"


def ensure_mitmproxy(timeout_s: float = 300.0) -> tuple[bool, str]:
    """Ensure mitmproxy is installed into the memor environment.

    Returns (ok, detail). System brew mitmdump alone is treated as insufficient.
    """
    if mitmproxy_available():
        bin_path = venv_mitmdump()
        return True, f"mitmproxy ready ({bin_path or 'python -c fallback'})"

    # Prefer pipx inject (pipx venvs often lack `python -m pip`)
    pipx = shutil.which("pipx")
    if pipx:
        try:
            proc = subprocess.run(
                [pipx, "inject", _pipx_package_name(), "mitmproxy>=10.0"],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except Exception as exc:
            return False, f"pipx inject failed: {exc}"
        if proc.returncode == 0 or mitmproxy_available() or mitmproxy_importable():
            if mitmproxy_available() or mitmproxy_importable():
                return True, "installed mitmproxy via pipx inject"
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = err[-1] if err else f"exit {proc.returncode}"
        # fall through to pip

    # Fallback: python -m pip (editable / venv installs)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "mitmproxy>=10.0"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except Exception as exc:
        return False, (
            f"auto-install failed: {exc}. "
            "Run: pipx inject memor-cli mitmproxy"
        )

    if proc.returncode == 0 and (mitmproxy_available() or mitmproxy_importable()):
        return True, "installed mitmproxy into memor environment"

    err = (proc.stderr or proc.stdout or "").strip().splitlines()
    tail = err[-1] if err else f"exit {proc.returncode}"
    brew_note = ""
    if find_mitm_bin():
        brew_note = (
            " Note: a system mitmdump was found, but it cannot load Memor's addon "
            "(standalone binary). Install into the memor env instead."
        )
    return False, (
        f"auto-install failed ({tail}). "
        f"Run: pipx inject memor-cli mitmproxy{brew_note}"
    )


def _mitm_common_args(listen_port: int, script: str) -> list[str]:
    return [
        "-s",
        script,
        "--mode",
        f"regular@{listen_port}",
        "--set",
        "block_global=false",
        "--set",
        "stream_large_bodies=1",
    ]


def build_mitmdump_argv(*, listen_port: int = 8080) -> list[str]:
    """Headless mitmdump argv safe for launchd.

    Important: do NOT use ``python -m mitmproxy.tools.dump`` — that module has no
    ``__main__`` and exits 0 immediately. Use the venv ``mitmdump`` console script
    (``mitmproxy.tools.main:mitmdump``).
    """
    if not mitmproxy_importable():
        raise RuntimeError(
            "mitmproxy is not installed in the memor environment. Install with:\n"
            "  memor install-proxy --agent cursor --wire   # auto-installs\n"
            "  # or: pipx inject memor-cli mitmproxy"
        )

    script = str(addon_path())
    common = _mitm_common_args(listen_port, script)

    venv_bin = venv_mitmdump()
    if venv_bin:
        return [venv_bin, *common]

    # Fallback when console script is missing but package is importable
    runner = (
        "import sys; from mitmproxy.tools.main import mitmdump; "
        "sys.argv = ['mitmdump'] + sys.argv[1:]; raise SystemExit(mitmdump())"
    )
    return [sys.executable, "-c", runner, *common]


def build_mitm_argv(
    *,
    listen_port: int = 8080,
    web_port: int = 8081,
    use_web: bool = True,
) -> list[str]:
    """CLI helper: mitmdump by default; optional mitmweb for debugging."""
    if not use_web:
        return build_mitmdump_argv(listen_port=listen_port)

    if not mitmproxy_importable():
        return build_mitmdump_argv(listen_port=listen_port)

    web = Path(sys.executable).resolve().parent / "mitmweb"
    if web.is_file():
        return [
            str(web),
            "-s",
            str(addon_path()),
            "--mode",
            f"regular@{listen_port}",
            "--web-port",
            str(web_port),
            "--set",
            "block_global=false",
            "--set",
            "stream_large_bodies=1",
        ]
    return build_mitmdump_argv(listen_port=listen_port)


def cursor_settings_hint(listen_port: int = 8080) -> list[str]:
    return [
        "Cursor settings (written by memor install-proxy --agent cursor --wire):",
        f'  "http.proxy": "http://127.0.0.1:{listen_port}"',
        '  "http.proxySupport": "override"',
        '  "http.proxyStrictSSL": false',
        '  "http.noProxy": "127.0.0.1,localhost,::1"',
        '  "cursor.general.disableHttp2": true',
        "Dashboard: Cursor Wire health chip + Proxy savings",
    ]
