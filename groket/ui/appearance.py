"""Host light/dark appearance (Linux, macOS, Windows)."""

from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Literal, Protocol, cast

Appearance = Literal["light", "dark"]


class _WinReg(Protocol):
    HKEY_CURRENT_USER: int

    def OpenKey(self, key: int, sub_key: str) -> object: ...

    def QueryValueEx(self, key: object, name: str) -> tuple[int, int]: ...


_PORTAL = [
    "gdbus",
    "call",
    "--session",
    "--dest=org.freedesktop.portal.Desktop",
    "--object-path=/org/freedesktop/portal/desktop",
    "--method=org.freedesktop.portal.Settings.Read",
    "org.freedesktop.appearance",
    "color-scheme",
]


def _cmd(args: list[str]) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=1, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def _winreg() -> _WinReg | None:
    try:
        return cast(_WinReg, importlib.import_module("winreg"))
    except ImportError:
        return None


def _windows_light() -> bool:
    wr = _winreg()
    if wr is None:
        return False
    try:
        key = wr.OpenKey(
            wr.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        raw, _unused = wr.QueryValueEx(key, "AppsUseLightTheme")
    except OSError:
        return False
    return raw == 1


def appearance() -> Appearance:
    """Current desktop appearance on this host.

    Linux portal: 1 dark, 2 light, 0 / missing light (same as icedtea).
    """
    if sys.platform == "darwin":
        style = _cmd(["defaults", "read", "-g", "AppleInterfaceStyle"]).strip().lower()
        return "dark" if style == "dark" else "light"
    if sys.platform == "win32":
        return "light" if _windows_light() else "dark"
    out = _cmd(_PORTAL)
    if "uint32 1" in out:
        return "dark"
    return "light"
