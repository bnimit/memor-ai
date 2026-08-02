"""mitmproxy CA ensure + macOS trust helpers."""
from __future__ import annotations

import platform
import subprocess
from pathlib import Path

MITMPROXY_CONFDIR = Path.home() / ".mitmproxy"
CA_CERT_NAME = "mitmproxy-ca-cert.pem"


def ca_cert_path(confdir: Path | None = None) -> Path:
    return (confdir or MITMPROXY_CONFDIR) / CA_CERT_NAME


def login_keychain_path() -> Path:
    """Current-user login keychain (macOS)."""
    modern = Path.home() / "Library" / "Keychains" / "login.keychain-db"
    if modern.exists():
        return modern
    return Path.home() / "Library" / "Keychains" / "login.keychain"


def system_keychain_path() -> Path:
    return Path("/Library/Keychains/System.keychain")


def ensure_ca_cert(confdir: Path | None = None) -> Path:
    """Return path to mitmproxy CA PEM, generating the store if missing."""
    conf = confdir or MITMPROXY_CONFDIR
    pem = ca_cert_path(conf)
    if pem.exists():
        return pem
    conf.mkdir(parents=True, exist_ok=True)
    try:
        from mitmproxy.certs import CertStore

        CertStore.from_store(path=str(conf), basename="mitmproxy", key_size=2048)
    except ImportError as exc:
        raise RuntimeError(
            "mitmproxy is required to generate the Cursor wire CA.\n"
            "  pipx inject memor-cli mitmproxy\n"
            "  # or: pip install 'memor-cli[cursor-wire]'"
        ) from exc
    if not pem.exists():
        raise RuntimeError(f"Failed to generate mitmproxy CA at {pem}")
    return pem


def _find_mitmproxy_in_keychain(keychain: Path) -> bool:
    if not keychain.exists() and "login.keychain" not in str(keychain):
        return False
    try:
        r = subprocess.run(
            [
                "security",
                "find-certificate",
                "-c",
                "mitmproxy",
                str(keychain),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.returncode == 0 and "mitmproxy" in (r.stdout + r.stderr)
    except Exception:
        return False


def ca_already_trusted_macos(pem: Path | None = None) -> bool:
    """True if a mitmproxy cert is trusted for this user (login) or system-wide."""
    if platform.system() != "Darwin":
        return False
    return _find_mitmproxy_in_keychain(login_keychain_path()) or _find_mitmproxy_in_keychain(
        system_keychain_path()
    )


def ca_trust_scope_macos() -> str | None:
    """Return 'login', 'system', both preference login, or None if not found."""
    if platform.system() != "Darwin":
        return None
    login = _find_mitmproxy_in_keychain(login_keychain_path())
    system = _find_mitmproxy_in_keychain(system_keychain_path())
    if login:
        return "login"
    if system:
        return "system"
    return None


def trust_ca_macos(pem: Path) -> tuple[bool, str]:
    """Add CA to the **login** keychain (user scope — no sudo / System keychain).

    Cursor runs as your user, so login-keychain trust is enough. Prefer this over
    System.keychain so the cert is not machine-wide and does not need admin.
    """
    if platform.system() != "Darwin":
        return False, "CA auto-trust is only automated on macOS"
    if not pem.exists():
        return False, f"CA cert not found: {pem}"

    scope = ca_trust_scope_macos()
    if scope == "login":
        return True, "CA already trusted in login keychain (user scope)"
    if scope == "system":
        return True, (
            "CA already trusted in System keychain (from an earlier install). "
            "That still works; new installs use the login keychain instead."
        )

    keychain = login_keychain_path()
    # No sudo: user-scoped trust. May still prompt for keychain password once.
    cmd = [
        "security",
        "add-trusted-cert",
        "-d",
        "-r",
        "trustRoot",
        "-k",
        str(keychain),
        str(pem),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as exc:
        return False, str(exc)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
        return False, detail
    return True, f"CA trusted in login keychain ({keychain.name}, user scope only)"


def linux_trust_instructions(pem: Path) -> list[str]:
    return [
        "Linux: trust the mitmproxy CA manually, then re-run install if needed:",
        f"  sudo cp {pem} /usr/local/share/ca-certificates/memor-mitmproxy.crt",
        "  sudo update-ca-certificates",
        "  # Or import into your browser/OS trust store.",
    ]
