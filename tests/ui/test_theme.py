"""Catalog Textual themes and host auto resolve."""

from __future__ import annotations

from anqa.ui.styles import status_rich_style, theme_is_light
from anqa.ui.theme import (
    THEME_FAMILIES,
    community_themes,
    family_of_theme,
    host_pair_themes,
    load_user_themes,
    register_catalog_themes,
    resolve_theme,
    theme_family_pairs,
    theme_in_pair,
)


def test_auto_uses_terminal_ansi_pair() -> None:
    assert resolve_theme("auto", "light") == "ansi-light"
    assert resolve_theme("", "dark") == "ansi-dark"
    assert resolve_theme("system", "light") == "ansi-light"
    assert resolve_theme("default", "dark") == "ansi-dark"
    assert resolve_theme("anqa", "dark") == "ansi-dark"
    assert resolve_theme("anqa-light", "light") == "ansi-light"


def test_ansi_pair_pins_unless_follow_os() -> None:
    assert resolve_theme("ansi-light", "dark", follow_os=False) == "ansi-light"
    assert resolve_theme("ansi-dark", "light", follow_os=False) == "ansi-dark"
    assert resolve_theme("ansi-light", "dark", follow_os=True) == "ansi-dark"
    assert resolve_theme("ansi-dark", "light", follow_os=True) == "ansi-light"
    assert resolve_theme("tokyo-night", "light", follow_os=False) == "tokyo-night"
    assert resolve_theme("tokyo-night", "light", follow_os=True) == "tokyo-night-day"


def test_ansi_light_paper_matches_host_light() -> None:
    faces = {t.name: t for t in host_pair_themes()}
    assert faces["ansi-light"].background == "#F3F3F3"
    assert faces["ansi-light"].dark is False
    assert faces["ansi-dark"].background == "#202020"
    assert faces["ansi-dark"].dark is True
    assert theme_in_pair("gruvbox")
    assert theme_in_pair("ansi-light")
    assert not theme_in_pair("nord")
    assert family_of_theme("gruvbox") == "gruvbox"
    assert family_of_theme("gruvbox-light") == "gruvbox"
    assert family_of_theme("tokyo-night-day") == "tokyo-night"
    assert family_of_theme("ansi-light") == "ansi"
    assert family_of_theme("nord") is None


def test_named_pair_follows_only_when_asked() -> None:
    assert resolve_theme("gruvbox", "light") == "gruvbox"
    assert resolve_theme("gruvbox", "dark", follow_os=True) == "gruvbox"
    assert resolve_theme("gruvbox", "light", follow_os=True) == "gruvbox-light"
    assert resolve_theme("gruvbox-light", "dark", follow_os=True) == "gruvbox"
    assert resolve_theme("gruvbox", "light", follow_os=False) == "gruvbox"
    assert resolve_theme("nord", "light") == "nord"
    assert resolve_theme("github-dark", "light") == "github-dark"
    assert resolve_theme("github-dark", "light", follow_os=True) == "github-light"
    assert resolve_theme("kanagawa-wave", "light", follow_os=True) == "kanagawa-lotus"


def test_register_catalog_includes_community() -> None:
    seen: list[str] = []

    class _App:
        def register_theme(self, theme: object) -> None:
            seen.append(getattr(theme, "name", ""))

    register_catalog_themes(_App())
    names = set(seen)
    assert "github-dark" in names
    assert "everforest-light" in names
    assert "kanagawa-wave" in names
    assert "ansi-light" in names
    assert "anqa" not in names
    assert {t.name for t in community_themes()} <= names


def test_user_theme_file(tmp_path) -> None:
    (tmp_path / "paper.toml").write_text(
        'foreground = "#111111"\nbackground = "#f7f4ef"\ndark = false\n',
        encoding="utf-8",
    )
    themes = load_user_themes(tmp_path)
    assert len(themes) == 1
    assert themes[0].name == "paper"
    assert themes[0].background == "#f7f4ef"
    assert themes[0].dark is False


def test_theme_family_pairs_include_textual_and_nightfox() -> None:
    import json
    from pathlib import Path

    pairs = theme_family_pairs()
    assert pairs["textual"] == ("textual-light", "textual-dark")
    assert pairs["nightfox"] == ("dawnfox", "nightfox")
    assert pairs["ansi"] == ("ansi-light", "ansi-dark")
    assert set(pairs) == set(THEME_FAMILIES)
    asset = json.loads(Path("desktop/assets/theme-pairs.json").read_text(encoding="utf-8"))
    assert {k: tuple(v) for k, v in asset.items()} == pairs


def test_status_roles_are_ansi() -> None:
    assert theme_is_light("catppuccin-latte")
    assert theme_is_light("rose-pine-dawn")
    assert theme_is_light("kanagawa-lotus")
    assert not theme_is_light("nord")
    assert status_rich_style("running") == "bold yellow"
    assert status_rich_style("completed") == "bold green"
    assert status_rich_style("failed") == "bold red"
