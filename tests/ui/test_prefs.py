"""UI prefs (show_tips) and admonition gating."""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.config import invalidate_config_cache
from groket.ui import prefs
from groket.ui.panel_render import tip_line


@pytest.fixture(autouse=True)
def _isolate_prefs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr("groket.paths.app_config_path", lambda: cfg)
    invalidate_config_cache()
    yield  # type: ignore[misc]  # fixture teardown requires yield
    invalidate_config_cache()


def test_show_tips_default_true() -> None:
    assert prefs.show_tips_enabled() is True
    assert "┌" in tip_line("hello").plain


def test_show_tips_off_hides_admonitions() -> None:
    prefs.set_show_tips(False)
    assert prefs.show_tips_enabled() is False
    assert tip_line("hello").plain == ""


def test_toggle_show_tips() -> None:
    assert prefs.toggle_show_tips() is False
    assert prefs.toggle_show_tips() is True


def test_host_and_auto_serve_defaults() -> None:
    assert prefs.show_host_sessions_enabled() is False
    assert prefs.auto_serve_enabled() is True


def test_hud_shortcut_reads_table(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        '[hud]\nglobal_shortcut = "Ctrl+Shift+G"\n',
        encoding="utf-8",
    )
    invalidate_config_cache()
    assert prefs.hud_global_shortcut() == "Ctrl+Shift+G"


def test_write_failure_keeps_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("groket.paths.app_config_path", lambda: Path("/dev/null/nope/config.toml"))
    invalidate_config_cache()
    prefs.set_show_tips(False)
    assert prefs.show_tips_enabled() is True


def test_invalid_toml_returns_defaults(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("not = [toml", encoding="utf-8")
    invalidate_config_cache()
    assert prefs.show_tips_enabled() is True
