"""VS Code / Cursor settings.json JSONC loading."""
from __future__ import annotations

from memor.proxy.vscode_settings import load_settings_json


def test_load_settings_json_strips_line_comments(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        '{\n'
        '  "editor.fontSize": 14,\n'
        '  //"http.proxy": "http://127.0.0.1:8081",\n'
        '  //"http.proxySupport": "override",\n'
        '  "openai.baseUrl": "http://127.0.0.1:8421/cursor/v1"\n'
        '}\n'
    )
    data = load_settings_json(path)
    assert data["editor.fontSize"] == 14
    assert data["openai.baseUrl"].endswith("/cursor/v1")
    assert "http.proxy" not in data


def test_strip_cursor_wire_removes_commented_keys(tmp_path, monkeypatch):
    from memor.cursor_wire import settings as wire_settings

    path = tmp_path / "settings.json"
    path.write_text(
        '{\n'
        '  "openai.baseUrl": "http://127.0.0.1:8421/cursor/v1",\n'
        '  //"http.proxy": "http://127.0.0.1:8081",\n'
        '  //"cursor.general.disableHttp2": true\n'
        '}\n'
    )
    backup = tmp_path / "backup.json"
    monkeypatch.setattr(
        wire_settings,
        "cursor_paths",
        lambda: (path, backup, "{}"),
    )
    msg = wire_settings.strip_cursor_wire_settings()
    assert "removed" in msg
    text = path.read_text()
    assert "http.proxy" not in text
    assert "//\"" not in text.replace(" ", "")
    assert "openai.baseUrl" in text
