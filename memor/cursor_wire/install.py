"""Full Cursor stack install: BYOK + wire mitmdump + hooks + CA + settings."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from memor.config import (
    cursor_wire_port as configured_wire_port,
    is_cursor_wire_enabled,
    set_cursor_wire,
    set_cursor_wire_port,
)
from memor.cursor_wire.ca import (
    ca_already_trusted_macos,
    ensure_ca_cert,
    linux_trust_instructions,
    trust_ca_macos,
)
from memor.cursor_wire.launch import ensure_mitmproxy, mitmproxy_available
from memor.cursor_wire.ports import resolve_wire_port, wait_for_wire_health
from memor.cursor_wire.settings import (
    existing_foreign_proxy,
    failover_cursor_wire,
    write_cursor_wire_settings,
)
from memor.proxy.cursor_install import cursor_paths, install_cursor_proxy
from memor.proxy.vscode_settings import load_settings_json


@dataclass
class CursorFullInstallResult:
    lines: list[str] = field(default_factory=list)
    wire_enabled: bool = False
    wire_port: int | None = None
    byok_ok: bool = False
    aborted: bool = False


def explain_cursor_core_install() -> list[str]:
    return [
        "This enables Cursor support (core stack):",
        "  • Memory recall (hooks, if missing)",
        "  • Shell output compression hooks",
        "  • BYOK proxy on 127.0.0.1:8421 (custom models)",
        "",
        "Optional later: subscription Composer token compression (mitmdump MITM).",
    ]


def explain_cursor_wire_opt_in() -> list[str]:
    return [
        "Subscription Composer wire compression:",
        "  • Runs local mitmdump (installs mitmproxy into the memor env if needed)",
        "  • Requires trusting a local CA (see next prompt — why explained there)",
        "  • Sets Cursor http.proxy to the wire port (localhost only)",
        "  • Decrypts Cursor → *.cursor.sh traffic on this machine only",
    ]


def explain_ca_trust_why(pem) -> list[str]:
    return [
        "Why CA trust is required:",
        "  Cursor talks to *.cursor.sh over HTTPS. Wire compression must decrypt",
        "  that traffic on this machine to shrink large Composer frames, then",
        "  re-encrypt to Cursor's servers. macOS will only allow that if you trust",
        "  the local mitmproxy CA (generated at):",
        f"    {pem}",
        "  Memor does not send your traffic to a third party — mitmdump runs locally.",
        "  Trust is installed in your **login** keychain (this user only — not System).",
        "  You can remove it later in Keychain Access (login keychain, search: mitmproxy).",
        "  macOS may ask for your keychain password once (no admin/sudo).",
    ]


# Back-compat alias for older imports/docs
def explain_cursor_full_install() -> list[str]:
    return explain_cursor_core_install() + [""] + explain_cursor_wire_opt_in()


def _ensure_memory_hooks() -> list[str]:
    lines: list[str] = []
    try:
        from memor.cli import _install_hook_logic
        from pathlib import Path
        import shutil

        hook_bin = shutil.which("memor-hook")
        if not hook_bin:
            lines.append("Memory hooks: memor-hook not on PATH — run: memor install-hook")
            return lines
        settings = Path.home() / ".claude" / "settings.json"
        # Only install if no memor-hook already present.
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


def install_cursor_full_stack(
    *,
    byok_port: int,
    upstream_url: str | None = None,
    no_wire: bool = False,
    wire: bool = False,
    yes: bool = False,
    skip_ca_trust: bool = False,
    confirm: Callable[[str], bool] | None = None,
    confirm_wire: Callable[[str], bool] | None = None,
) -> CursorFullInstallResult:
    """Orchestrate Cursor install. Wire/mitmdump is opt-in (prompt, --wire, or --no-wire).

    ``confirm`` defaults to Yes; ``confirm_wire`` defaults to No when interactive.
    ``--yes`` accepts core prompts but does **not** enable wire unless ``--wire``.
    """
    import platform

    from memor import service

    result = CursorFullInstallResult()
    ask = confirm or (lambda _m: yes)
    ask_wire = confirm_wire or confirm or (lambda _m: False)

    result.lines.extend(explain_cursor_core_install())
    if not yes:
        if not ask("Continue with Cursor core install (hooks + BYOK)?"):
            result.aborted = True
            result.lines.append("Aborted.")
            return result

    # BYOK always (existing path) — backup + base URL keys
    manual = install_cursor_proxy(byok_port, upstream_url=upstream_url)
    result.byok_ok = True
    result.lines.append("BYOK proxy: Cursor base URL keys updated")
    result.lines.extend(manual)

    result.lines.extend(_ensure_memory_hooks())
    result.lines.extend(_ensure_shell_hooks())

    want_wire = False
    if no_wire and wire:
        result.lines.append("Wire MITM: --wire and --no-wire both set; skipping wire.")
        want_wire = False
    elif no_wire:
        result.lines.append("Wire MITM: skipped (--no-wire)")
        want_wire = False
    elif wire:
        result.lines.append("Wire MITM: enabled (--wire)")
        want_wire = True
    elif yes:
        # Non-interactive: never surprise-enable MITM
        result.lines.append(
            "Wire MITM: skipped (-y does not enable MITM; pass --wire to opt in)"
        )
        want_wire = False
    else:
        result.lines.extend(explain_cursor_wire_opt_in())
        want_wire = bool(
            ask_wire(
                "Also enable subscription Composer token compression "
                "(installs mitmdump + CA trust)?"
            )
        )
        if not want_wire:
            result.lines.append("Wire MITM: skipped (declined)")

    if not want_wire:
        set_cursor_wire(False)
        result.lines.append(service.install(with_dashboard=True, with_proxy=True))
        return result

    ok_dep, dep_detail = ensure_mitmproxy()
    result.lines.append(f"Wire dependency: {dep_detail}")
    if not ok_dep or not mitmproxy_available():
        set_cursor_wire(False)
        result.lines.append(
            "Wire MITM: mitmproxy not available in the memor environment — skipped.\n"
            "  (Homebrew mitmdump alone cannot load Memor's addon.)\n"
            "  Fallback: pipx inject memor-cli mitmproxy\n"
            "  Then re-run: memor install-proxy --agent cursor --wire"
        )
        result.lines.append(service.install(with_dashboard=True, with_proxy=True))
        return result

    try:
        pem = ensure_ca_cert()
        result.lines.append(f"Wire CA: {pem}")
    except Exception as exc:
        set_cursor_wire(False)
        result.lines.append(f"Wire MITM: CA generation failed — {exc}")
        return result

    system = platform.system()
    if skip_ca_trust:
        result.lines.append(
            "Wire CA trust: skipped (--skip-ca-trust). "
            "Wire will not work until the CA is trusted."
        )
    elif system == "Darwin":
        if ca_already_trusted_macos(pem):
            result.lines.append("Wire CA trust: already trusted")
        else:
            result.lines.extend(explain_ca_trust_why(pem))
            if not ask("Trust the local mitmproxy CA in your login keychain now?"):
                set_cursor_wire(False)
                result.lines.append(
                    "Wire MITM: skipped (CA not trusted). BYOK + Shell hooks remain."
                )
                result.lines.append(service.install(with_dashboard=True, with_proxy=True))
                return result
            ok, detail = trust_ca_macos(pem)
            result.lines.append(f"Wire CA trust: {detail}")
            if not ok:
                set_cursor_wire(False)
                result.lines.append(
                    "Wire MITM: aborted (CA trust failed). BYOK + Shell hooks remain."
                )
                result.lines.append(service.install(with_dashboard=True, with_proxy=True))
                return result
    else:
        result.lines.extend(explain_ca_trust_why(pem))
        result.lines.extend(linux_trust_instructions(pem))
        if not ask("Continue wire install after trusting the CA manually?"):
            set_cursor_wire(False)
            result.lines.append("Wire MITM: skipped.")
            result.lines.append(service.install(with_dashboard=True, with_proxy=True))
            return result

    # Foreign proxy warning
    config_path, _, _ = cursor_paths()
    settings = load_settings_json(config_path)
    foreign = existing_foreign_proxy(settings)
    if foreign:
        result.lines.append(f"Existing http.proxy detected: {foreign}")
        if not ask("Replace your existing Cursor http.proxy with Memor wire MITM?"):
            set_cursor_wire(False)
            result.lines.append("Wire MITM: skipped (kept existing proxy).")
            result.lines.append(service.install(with_dashboard=True, with_proxy=True))
            return result

    try:
        preferred = configured_wire_port() if is_cursor_wire_enabled() else None
        from memor.config import load_config

        cfg_port = load_config().get("cursor_wire_port")
        if preferred is None and cfg_port:
            preferred = int(cfg_port)
        wire_port, port_notes = resolve_wire_port(preferred=preferred)
    except Exception as exc:
        set_cursor_wire(False)
        result.lines.append(f"Wire MITM: port selection failed — {exc}")
        result.lines.append(service.install(with_dashboard=True, with_proxy=True))
        return result

    result.lines.extend(port_notes)
    set_cursor_wire_port(wire_port)
    result.wire_port = wire_port

    # Start services including wire unit (flag temporarily true so unit installs)
    set_cursor_wire(True)
    result.lines.append("Starting Memor services (including cursor-wire mitmdump)...")
    result.lines.append(service.install(with_dashboard=True, with_proxy=True))

    ok, detail = wait_for_wire_health(wire_port)
    if not ok:
        for line in failover_cursor_wire(detail):
            result.lines.append(line)
        log_path = getattr(service, "CURSOR_WIRE_LOG", None)
        result.lines.append(
            "Wire MITM: mitmdump failed health check — wire settings not applied. "
            "BYOK + Shell hooks remain."
        )
        if log_path is not None:
            result.lines.append(f"  See log: {log_path}")
            try:
                from pathlib import Path

                text = Path(log_path).read_text(errors="replace").strip()
                if text:
                    tail = "\n".join(text.splitlines()[-12:])
                    result.lines.append("  --- cursor-wire.log (tail) ---")
                    result.lines.append(tail)
                else:
                    result.lines.append(
                        "  (log empty — mitmdump likely exited immediately; "
                        "re-run after upgrading memor, or: memor cursor-wire-mitm --dump)"
                    )
            except Exception:
                pass
        result.wire_enabled = False
        return result

    write_cursor_wire_settings(wire_port)
    result.wire_enabled = True
    result.lines.append(
        f"Wire MITM: healthy on :{wire_port}; Cursor http.proxy + noProxy written"
    )
    result.lines.append("Restart Cursor for settings to take effect.")
    result.lines.append(
        "Dashboard: http://localhost:8420 — Cursor Wire chip + Proxy savings"
    )
    return result

