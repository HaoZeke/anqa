"""Catalog themes for the terminal app (host default plus named colorways)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Protocol

from textual.theme import Theme

from ..models import JsonObject, as_json_object
from ..paths import user_themes_dir
from .appearance import Appearance

# Preference aliases that mean "follow the host". Pair members (ansi-light)
# are catalog names, not aliases.
AUTO_NAMES = frozenset(
    {
        "",
        "auto",
        "system",
        "default",
        "groket",
        "groket-light",
    }
)

THEME_FAMILIES: dict[str, tuple[str, str]] = {
    "ansi": ("ansi-light", "ansi-dark"),
    "atom-one": ("atom-one-light", "atom-one-dark"),
    "ayu": ("ayu-light", "ayu-dark"),
    "catppuccin": ("catppuccin-latte", "catppuccin-mocha"),
    "everforest": ("everforest-light", "everforest-dark"),
    "github": ("github-light", "github-dark"),
    "gruvbox": ("gruvbox-light", "gruvbox"),
    "kanagawa": ("kanagawa-lotus", "kanagawa-wave"),
    "modus": ("modus-operandi", "modus-vivendi"),
    "nightfox": ("dawnfox", "nightfox"),
    "rose-pine": ("rose-pine-dawn", "rose-pine"),
    "solarized": ("solarized-light", "solarized-dark"),
    "textual": ("textual-light", "textual-dark"),
    "tokyo-night": ("tokyo-night-day", "tokyo-night"),
}
_PAIRS: dict[str, tuple[str, str]] = {}
_FAMILY: dict[str, str] = {}
for _id, _pair in THEME_FAMILIES.items():
    _PAIRS[_id] = _pair
    _PAIRS[_pair[0]] = _pair
    _PAIRS[_pair[1]] = _pair
    _FAMILY[_id] = _id
    _FAMILY[_pair[0]] = _id
    _FAMILY[_pair[1]] = _id


def _hex(rec: JsonObject, *keys: str, default: str) -> str:
    for key in keys:
        raw = rec.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return default


def theme_from_mapping(name: str, rec: JsonObject) -> Theme:
    """Build a Textual theme from catalog or user-file tokens."""
    dark = rec.get("dark")
    if not isinstance(dark, bool):
        dark = True
    return Theme(
        name=name,
        primary=_hex(rec, "primary", default="#0178D4"),
        secondary=_hex(rec, "secondary", "muted", default="#928374"),
        accent=_hex(rec, "accent", default="#D79921"),
        warning=_hex(rec, "warning", default="#D79921"),
        error=_hex(rec, "error", "danger", default="#CC241D"),
        success=_hex(rec, "success", default="#98971A"),
        foreground=_hex(rec, "foreground", "text", default="#F2F2F2"),
        background=_hex(rec, "background", "canvas", default="#1E1E1E"),
        surface=_hex(rec, "surface", default="#282828"),
        panel=_hex(rec, "panel", default="#3C3836"),
        dark=dark,
    )


def community_themes() -> list[Theme]:
    """Popular named colorways Textual does not ship."""
    raw = resources.files("groket.ui").joinpath("community_themes.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        return []
    out: list[Theme] = []
    for name, rec in data.items():
        if isinstance(name, str) and isinstance(rec, dict):
            out.append(theme_from_mapping(name, as_json_object(rec)))
    return out


def load_user_themes(root: Path | None = None) -> list[Theme]:
    """Themes from ``~/.groket/themes/*.toml`` (Kitty-style drop-in files)."""
    import tomllib

    folder = root if root is not None else user_themes_dir()
    if not folder.is_dir():
        return []
    out: list[Theme] = []
    try:
        files = sorted(folder.glob("*.toml"))
    except OSError:
        return []
    for path in files:
        try:
            rec = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("name") or path.stem).strip() or path.stem
        out.append(theme_from_mapping(name, as_json_object(rec)))
    return out


def host_pair_themes() -> list[Theme]:
    """``ansi-light`` / ``ansi-dark`` paper matching icedtea ``light`` / ``dark``."""
    return [
        Theme(
            name="ansi-light",
            primary="#2563EB",
            secondary="#5C5C5C",
            accent="#0F766E",
            warning="#9A6700",
            error="#B42318",
            success="#1B7F3A",
            foreground="#1A1A1A",
            background="#F3F3F3",
            surface="#FFFFFF",
            panel="#EBEBEB",
            dark=False,
        ),
        Theme(
            name="ansi-dark",
            primary="#6B9EFF",
            secondary="#A3A3A3",
            accent="#5EEAD4",
            warning="#FBBF24",
            error="#F87171",
            success="#4ADE80",
            foreground="#F2F2F2",
            background="#202020",
            surface="#2B2B2B",
            panel="#2B2B2B",
            dark=True,
        ),
    ]


def theme_family_pairs() -> dict[str, tuple[str, str]]:
    """Family id → ``(light member, dark member)``. Same table the HUD reads."""
    return dict(THEME_FAMILIES)


def theme_in_pair(name: str) -> bool:
    """True when *name* is a light/dark catalog pair member."""
    return (name or "").strip() in _PAIRS


def family_of_theme(name: str) -> str | None:
    """Pair id for *name* (``gruvbox-light`` → ``gruvbox``), or None."""
    key = (name or "").strip()
    return _FAMILY.get(key)


class ThemeHost(Protocol):
    """Object that can take a Textual catalog theme."""

    def register_theme(self, theme: Theme) -> None: ...


def register_catalog_themes(app: ThemeHost, *, user_root: Path | None = None) -> None:
    """Register community and user themes on a Textual app.

    :param app: ``App`` (or test stub) with ``register_theme``.
    :param user_root: Optional themes directory (tests).
    """
    for theme in host_pair_themes():
        app.register_theme(theme)
    for theme in community_themes():
        app.register_theme(theme)
    for theme in load_user_themes(user_root):
        app.register_theme(theme)


def resolve_theme(pref: str, desktop: Appearance, *, follow_os: bool = False) -> str:
    """Concrete catalog name for *pref*.

    ``auto`` / empty follows the look passed in (terminal, then desktop).
    A named pair flips with that look only when *follow_os* is on.
    """
    key = (pref or "").strip()
    if key.casefold() in AUTO_NAMES:
        return "ansi-light" if desktop == "light" else "ansi-dark"
    if not follow_os:
        return key
    pair = _PAIRS.get(key)
    if pair is None:
        return key
    return pair[0] if desktop == "light" else pair[1]
