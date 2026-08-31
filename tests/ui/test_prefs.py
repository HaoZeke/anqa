"""UI prefs accessors."""

from __future__ import annotations

from pathlib import Path

import pytest
from anqa.config import invalidate_config_cache
from anqa.ui import prefs


@pytest.fixture(autouse=True)
def _isolate_prefs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr("anqa.paths.app_config_path", lambda: cfg)
    invalidate_config_cache()
    yield  # type: ignore[misc]  # fixture teardown requires yield
    invalidate_config_cache()


def test_auto_anqad_default() -> None:
    assert prefs.auto_anqad_enabled() is True


def test_hud_shortcut_reads_table(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        '[hud]\nglobal_shortcut = "Ctrl+Shift+G"\n',
        encoding="utf-8",
    )
    invalidate_config_cache()
    assert prefs.hud_global_shortcut() == "Ctrl+Shift+G"


def test_write_failure_keeps_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anqa.paths.app_config_path", lambda: Path("/dev/null/nope/config.toml"))
    invalidate_config_cache()
    prefs.set_auto_anqad(False)
    assert prefs.auto_anqad_enabled() is True


def test_invalid_toml_returns_defaults(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("not = [toml", encoding="utf-8")
    invalidate_config_cache()
    assert prefs.auto_anqad_enabled() is True
