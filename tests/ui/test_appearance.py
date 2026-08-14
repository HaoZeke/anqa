"""Host appearance on Linux, macOS, and Windows."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from groket.ui.appearance import _cmd, _windows_light, _winreg, appearance


class _Proc:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_appearance_by_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("groket.ui.appearance.sys.platform", "darwin")
    monkeypatch.setattr("groket.ui.appearance._cmd", lambda args: "Dark\n")
    assert appearance() == "dark"
    monkeypatch.setattr("groket.ui.appearance._cmd", lambda args: "")
    assert appearance() == "light"
    monkeypatch.setattr("groket.ui.appearance.sys.platform", "win32")
    monkeypatch.setattr("groket.ui.appearance._windows_light", lambda: True)
    assert appearance() == "light"
    monkeypatch.setattr("groket.ui.appearance._windows_light", lambda: False)
    assert appearance() == "dark"
    monkeypatch.setattr("groket.ui.appearance.sys.platform", "linux")
    monkeypatch.setattr("groket.ui.appearance._cmd", lambda args: "(<<uint32 2>>,)")
    assert appearance() == "light"
    monkeypatch.setattr("groket.ui.appearance._cmd", lambda args: "(<<uint32 1>>,)")
    assert appearance() == "dark"
    monkeypatch.setattr("groket.ui.appearance._cmd", lambda args: "(<<uint32 0>>,)")
    assert appearance() == "light"
    monkeypatch.setattr("groket.ui.appearance._cmd", lambda args: "")
    assert appearance() == "light"


def test_cmd_stdout_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("groket.ui.appearance.subprocess.run", lambda *a, **k: _Proc(0, "ok"))
    assert _cmd(["true"]) == "ok"
    monkeypatch.setattr("groket.ui.appearance.subprocess.run", lambda *a, **k: _Proc(1, "no"))
    assert _cmd(["false"]) == ""

    def _os(*a: object, **k: object) -> Any:
        raise OSError("missing")

    def _to(*a: object, **k: object) -> Any:
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr("groket.ui.appearance.subprocess.run", _os)
    assert _cmd(["x"]) == ""
    monkeypatch.setattr("groket.ui.appearance.subprocess.run", _to)
    assert _cmd(["x"]) == ""


def test_windows_light(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("groket.ui.appearance._winreg", lambda: None)
    assert _windows_light() is False

    class _Missing:
        HKEY_CURRENT_USER = 1

        @staticmethod
        def OpenKey(*a: object, **k: object) -> object:
            raise OSError("missing")

        QueryValueEx = OpenKey

    monkeypatch.setattr("groket.ui.appearance._winreg", lambda: _Missing)
    assert _windows_light() is False

    class _Light:
        HKEY_CURRENT_USER = 1

        @staticmethod
        def OpenKey(*a: object, **k: object) -> object:
            return object()

        @staticmethod
        def QueryValueEx(*a: object, **k: object) -> tuple[int, int]:
            return (1, 4)

    monkeypatch.setattr("groket.ui.appearance._winreg", lambda: _Light)
    assert _windows_light() is True

    class _Dark:
        HKEY_CURRENT_USER = 1

        @staticmethod
        def OpenKey(*a: object, **k: object) -> object:
            return object()

        @staticmethod
        def QueryValueEx(*a: object, **k: object) -> tuple[int, int]:
            return (0, 4)

    monkeypatch.setattr("groket.ui.appearance._winreg", lambda: _Dark)
    assert _windows_light() is False


def test_winreg_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("groket.ui.appearance.importlib.import_module", lambda name: object())
    assert _winreg() is not None


def test_winreg_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _imp(name: str) -> Any:
        raise ImportError("no winreg")

    monkeypatch.setattr("groket.ui.appearance.importlib.import_module", _imp)
    assert _winreg() is None
