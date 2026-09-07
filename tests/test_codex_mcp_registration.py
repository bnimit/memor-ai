"""Tests for Codex's memor MCP registration.

The registration is what makes Codex able to read memory at all, and the agent
label inside it is what makes those reads attributable. Both were previously
untested, and the missing label meant Codex's recalls were filed under jcode.
"""
from __future__ import annotations

import pathlib
import tomllib

import pytest

from memor.proxy.install import register_mcp_codex

_BINARY = "/usr/local/bin/memor-retrieve-mcp"


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    """Point $HOME and the binary lookup at the sandbox."""
    monkeypatch.setattr(
        pathlib.Path, "home", staticmethod(lambda: tmp_path)
    )
    monkeypatch.setattr("shutil.which", lambda _name: _BINARY)
    return tmp_path / ".codex" / "config.toml"


def _server(config_path: pathlib.Path) -> dict:
    return tomllib.loads(config_path.read_text())["mcp_servers"]["memor_retrieve"]


def test_registration_labels_recalls_as_codex(codex_home) -> None:
    """Without this the MCP server falls back to its jcode default.

    That is what made Codex look like it never read: the reads happened and
    were filed under another agent's name.
    """
    register_mcp_codex()

    assert _server(codex_home)["env"]["MEMOR_HOOK_AGENT"] == "codex"


def test_registration_writes_valid_toml(codex_home) -> None:
    """An inline table written by hand is easy to get syntactically wrong."""
    register_mcp_codex()

    parsed = tomllib.loads(codex_home.read_text())

    assert parsed["mcp_servers"]["memor_retrieve"]["command"] == _BINARY


def test_existing_registration_gains_the_label(codex_home) -> None:
    """Upgrade path: installs made before the label existed must be fixed."""
    codex_home.parent.mkdir(parents=True)
    codex_home.write_text(
        '[mcp_servers.memor_retrieve]\ncommand = "/old/path"\n'
    )

    register_mcp_codex()

    server = _server(codex_home)
    assert server["env"]["MEMOR_HOOK_AGENT"] == "codex"
    assert server["command"] == _BINARY


def test_a_wrong_existing_label_is_corrected(codex_home) -> None:
    """A stale label would keep misattributing every Codex recall."""
    codex_home.parent.mkdir(parents=True)
    codex_home.write_text(
        "[mcp_servers.memor_retrieve]\n"
        f'command = "{_BINARY}"\n'
        'env = { MEMOR_HOOK_AGENT = "jcode" }\n'
    )

    register_mcp_codex()

    assert _server(codex_home)["env"]["MEMOR_HOOK_AGENT"] == "codex"


def test_unrelated_codex_config_is_preserved(codex_home) -> None:
    """This edits a file the user owns; it must not eat their settings."""
    codex_home.parent.mkdir(parents=True)
    codex_home.write_text(
        'model = "gpt-5"\n\n'
        '[mcp_servers.other]\ncommand = "/other"\n'
    )

    register_mcp_codex()

    parsed = tomllib.loads(codex_home.read_text())
    assert parsed["model"] == "gpt-5"
    assert parsed["mcp_servers"]["other"]["command"] == "/other"
    assert parsed["mcp_servers"]["memor_retrieve"]["command"] == _BINARY


def test_reregistering_does_not_duplicate_the_label(codex_home) -> None:
    register_mcp_codex()
    register_mcp_codex()
    register_mcp_codex()

    text = codex_home.read_text()
    assert text.count("MEMOR_HOOK_AGENT") == 1
    assert _server(codex_home)["env"]["MEMOR_HOOK_AGENT"] == "codex"


def test_missing_binary_is_an_explicit_error(codex_home, monkeypatch) -> None:
    """Silently writing a broken registration is the worse failure."""
    monkeypatch.setattr("shutil.which", lambda _name: None)

    with pytest.raises(RuntimeError, match="memor-retrieve-mcp"):
        register_mcp_codex()
