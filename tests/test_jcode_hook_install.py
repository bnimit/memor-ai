"""Installing the jcode hook must edit config.toml surgically and idempotently.

jcode's config.toml is the user's own file, full of keybindings and settings
memor knows nothing about. Installing a hook must add two keys to the existing
``[hooks]`` table and change nothing else, however many times it is run.
"""
from __future__ import annotations

from memor.cli import AGENT_CHOICES, _install_hook_logic_jcode

SAMPLE = """\
[keybindings]
scroll_up = "ctrl+shift+k"

[hooks]
pre_tool_timeout_ms = 5000

[ambient]
enabled = false
"""


def _install(tmp_path, text=SAMPLE, cmd="/usr/local/bin/memor-jcode-hook"):
    cfg = tmp_path / "config.toml"
    cfg.write_text(text)
    _install_hook_logic_jcode(cfg, cmd)
    return cfg.read_text()


def test_jcode_is_an_installable_agent():
    assert "jcode" in {k for k, _, _ in AGENT_CHOICES}


def test_adds_hooks_inside_the_existing_table(tmp_path):
    out = _install(tmp_path)
    assert "turn_end" in out and "session_end" in out
    hooks_body = out.split("[hooks]")[1].split("[ambient]")[0]
    assert "turn_end" in hooks_body, "hook keys landed outside the [hooks] table"
    assert "session_end" in hooks_body


def test_preserves_unrelated_config(tmp_path):
    out = _install(tmp_path)
    assert 'scroll_up = "ctrl+shift+k"' in out
    assert "pre_tool_timeout_ms = 5000" in out
    assert "[ambient]" in out and "enabled = false" in out


def test_is_idempotent(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(SAMPLE)
    for _ in range(3):
        _install_hook_logic_jcode(cfg, "/usr/local/bin/memor-jcode-hook")
    out = cfg.read_text()
    assert out.count("turn_end") == 1, "reinstall duplicated the hook"
    assert out.count("session_end") == 1


def test_creates_the_hooks_table_when_absent(tmp_path):
    out = _install(tmp_path, text='[display]\nemoji = true\n')
    assert "[hooks]" in out
    assert "turn_end" in out
    assert "emoji = true" in out


def test_does_not_clobber_a_users_own_turn_start(tmp_path):
    """Only memor's own lines are replaced; the user's hooks stay."""
    text = SAMPLE.replace(
        "pre_tool_timeout_ms = 5000",
        'pre_tool_timeout_ms = 5000\nturn_start = "/home/me/bin/my-own-hook"',
    )
    out = _install(tmp_path, text=text)
    assert "/home/me/bin/my-own-hook" in out


def test_result_is_valid_toml(tmp_path):
    import tomllib

    out = _install(tmp_path)
    parsed = tomllib.loads(out)
    assert "memor-jcode-hook" in parsed["hooks"]["turn_end"]
    assert parsed["hooks"]["pre_tool_timeout_ms"] == 5000
    assert parsed["keybindings"]["scroll_up"] == "ctrl+shift+k"
