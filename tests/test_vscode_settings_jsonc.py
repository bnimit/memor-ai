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


def _point_cursor_paths_at(monkeypatch, path, tmp_path):
    from memor.proxy import cursor_install

    monkeypatch.setattr(
        cursor_install, "cursor_paths", lambda: (path, tmp_path / "backup.json", "{}")
    )
    return cursor_install


def test_strip_legacy_wire_settings_removes_localhost_proxy_keys(tmp_path, monkeypatch):
    """Upgrading users must not be left pointing Cursor at a proxy we removed."""
    path = tmp_path / "settings.json"
    path.write_text(
        '{\n'
        '  "openai.baseUrl": "http://127.0.0.1:8421/cursor/v1",\n'
        '  "http.proxy": "http://127.0.0.1:8080",\n'
        '  "cursor.general.disableHttp2": true\n'
        '}\n'
    )
    cursor_install = _point_cursor_paths_at(monkeypatch, path, tmp_path)
    assert cursor_install.strip_legacy_wire_settings() is True
    text = path.read_text()
    assert "http.proxy" not in text
    assert "disableHttp2" not in text
    assert "openai.baseUrl" in text


def test_strip_legacy_wire_settings_leaves_a_corporate_proxy_alone(tmp_path, monkeypatch):
    """A proxy we did not write is the user's own — never touch it."""
    path = tmp_path / "settings.json"
    path.write_text(
        '{\n'
        '  "http.proxy": "http://proxy.corp.example:3128",\n'
        '  "http.proxyStrictSSL": true\n'
        '}\n'
    )
    cursor_install = _point_cursor_paths_at(monkeypatch, path, tmp_path)
    assert cursor_install.strip_legacy_wire_settings() is False
    assert "proxy.corp.example" in path.read_text()


def test_strip_legacy_wire_settings_noop_without_settings_file(tmp_path, monkeypatch):
    path = tmp_path / "missing.json"
    cursor_install = _point_cursor_paths_at(monkeypatch, path, tmp_path)
    assert cursor_install.strip_legacy_wire_settings() is False
