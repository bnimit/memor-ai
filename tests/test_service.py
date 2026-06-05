import platform
from unittest.mock import patch
from memor.service import _plist_content, _systemd_unit, LABEL, LOG_FILE


def test_plist_content():
    plist = _plist_content("/usr/local/bin/memor")
    assert LABEL in plist
    assert "/usr/local/bin/memor" in plist
    assert "<string>daemon</string>" in plist
    assert "<key>KeepAlive</key>" in plist
    assert str(LOG_FILE) in plist


def test_systemd_unit():
    unit = _systemd_unit("/usr/local/bin/memor")
    assert "/usr/local/bin/memor daemon" in unit
    assert "Restart=on-failure" in unit
    assert str(LOG_FILE) in unit


def test_is_macos():
    from memor.service import _is_macos
    expected = platform.system() == "Darwin"
    assert _is_macos() == expected
