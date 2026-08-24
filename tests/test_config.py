"""Canonical ``~/.groket/config.toml`` load, save, and comment keep."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit
from groket.config import (
    SCHEMA_ID,
    config_dump,
    emit_config_schema,
    invalidate_config_cache,
    load_app_config,
    parse_app_config,
    save_app_config,
    update_app_config,
    validate_config_file,
)


@pytest.fixture(autouse=True)
def _iso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("groket.paths.app_config_path", lambda: tmp_path / "config.toml")
    invalidate_config_cache()
    yield
    invalidate_config_cache()


def test_defaults_when_missing() -> None:
    cfg = load_app_config()
    assert cfg.theme == "auto"
    assert cfg.follow_os is False
    assert cfg.auto_serve is True
    assert cfg.live_refresh_workers == 1
    assert cfg.hud.window_mode is False
    assert cfg.hud.global_shortcut == ""
    assert cfg.hud.desktop_notifications is True
    assert cfg.export.default_profile == ""
    assert cfg.harness.host == ["grok"]


def test_save_writes_toml_tables(tmp_path: Path) -> None:
    save_app_config(parse_app_config({"theme": "nord", "hud": {"global_shortcut": "Ctrl+K"}}))
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert SCHEMA_ID in text
    data = tomlkit.parse(text)
    assert data["theme"] == "nord"
    assert data["hud"]["global_shortcut"] == "Ctrl+K"
    assert data["live_refresh_workers"] == 1
    assert "export" in data
    assert list(data["harness"]["host"]) == ["grok"]


def test_update_keeps_comment(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '# keep me\ntheme = "nord"\nextra_pref = true\n',
        encoding="utf-8",
    )
    invalidate_config_cache()
    update_app_config(theme="gruvbox")
    text = path.read_text(encoding="utf-8")
    assert "keep me" in text
    data = tomlkit.parse(text)
    assert data["theme"] == "gruvbox"
    assert data["extra_pref"] is True


def test_invalid_toml_returns_defaults(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("not = [toml", encoding="utf-8")
    invalidate_config_cache()
    cfg = load_app_config()
    assert cfg.theme == "auto"


def test_harness_host_normalizes_ids() -> None:
    cfg = parse_app_config({"harness": {"host": ["Grok", "grok", "demo"]}})
    assert cfg.harness.host == ["grok", "demo"]


def test_dump_roundtrip() -> None:
    cfg = parse_app_config({"follow_os": True, "hud": {"window_mode": True}})
    restored = parse_app_config(config_dump(cfg))
    assert restored.follow_os is True
    assert restored.hud.window_mode is True


def test_imports_json_when_toml_missing(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        '{"theme": "nord", "show_host_sessions": true, '
        '"hud_global_shortcut": "Ctrl+K", '
        '"analysis": {"live_refresh_workers": 3}}\n',
        encoding="utf-8",
    )
    invalidate_config_cache()
    cfg = load_app_config()
    assert cfg.theme == "nord"
    assert cfg.hud.global_shortcut == "Ctrl+K"
    assert cfg.live_refresh_workers == 1
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "nord" in text
    assert not (tmp_path / "config.json").exists()


def test_save_keeps_unknown_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('theme = "nord"\nextra_pref = true\n', encoding="utf-8")
    invalidate_config_cache()
    save_app_config(load_app_config())
    text = path.read_text(encoding="utf-8")
    assert "extra_pref" in text
    assert load_app_config().theme == "nord"


def test_toml_wins_over_sibling_json(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('theme = "gruvbox"\n', encoding="utf-8")
    (tmp_path / "config.json").write_text('{"theme": "nord"}\n', encoding="utf-8")
    invalidate_config_cache()
    assert load_app_config().theme == "gruvbox"


def test_schema_has_published_id() -> None:
    text = emit_config_schema()
    assert SCHEMA_ID in text
    for key in (
        "theme",
        "follow_os",
        "auto_serve",
        "live_refresh_workers",
        "hud",
        "export",
        "global_shortcut",
        "default_profile",
    ):
        assert key in text


def test_validate_example_file() -> None:
    path = Path("examples/config/config.toml")
    cfg = validate_config_file(path)
    assert cfg.theme == "auto"
    assert cfg.follow_os is False
    assert cfg.auto_serve is True
    assert cfg.live_refresh_workers == 1
    assert cfg.hud.desktop_notifications is True
    assert cfg.export.default_profile == ""


def test_show_host_sessions_is_dropped(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('show_host_sessions = false\ntheme = "nord"\n', encoding="utf-8")
    invalidate_config_cache()
    cfg = load_app_config()
    assert cfg.theme == "nord"
    save_app_config(cfg)
    text = path.read_text(encoding="utf-8")
    assert "show_host_sessions" not in text


def test_analysis_table_does_not_set_workers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[analysis]\nlive_refresh_workers = 4\n", encoding="utf-8")
    invalidate_config_cache()
    cfg = load_app_config()
    assert cfg.live_refresh_workers == 1
    save_app_config(cfg)
    text = path.read_text(encoding="utf-8")
    data = tomlkit.parse(text)
    assert data["live_refresh_workers"] == 1
    assert "analysis" not in data


def test_validate_rejects_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("not = [toml", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid TOML"):
        validate_config_file(path)


def test_validate_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is not a file"):
        validate_config_file(tmp_path / "missing.toml")
