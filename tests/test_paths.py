"""Tests for path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from anqa.paths import (
    app_config_path,
    app_home,
    cache_dir,
    default_host_sessions_root,
    is_run_dir_name,
    notes_fallback_dir,
    reports_dir,
    resolve_catalog_root,
    strip_run_prefix,
    traces_root_for_reload,
    user_export_profiles_dir,
    user_keys_path,
    user_themes_dir,
)


class TestIsRunDirName:
    def test_valid(self) -> None:
        assert is_run_dir_name("anqa-abc123-dietcoke") is True
        assert is_run_dir_name("anqa-x") is True

    def test_invalid(self) -> None:
        assert is_run_dir_name("") is False
        assert is_run_dir_name("other-prefix") is False
        assert is_run_dir_name("traces") is False
        assert is_run_dir_name("groket-old") is False


class TestStripRunPrefix:
    def test_strip(self) -> None:
        assert strip_run_prefix("anqa-abc123-dietcoke") == "abc123-dietcoke"

    def test_no_prefix(self) -> None:
        assert strip_run_prefix("something-else") == "something-else"


class TestResolveCatalogRoot:
    def test_none_uses_host_sessions(self) -> None:
        root = resolve_catalog_root(None)
        assert root == default_host_sessions_root()

    def test_host_sessions_tree(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        host = tmp_path / ".grok" / "sessions"
        host.mkdir(parents=True)
        monkeypatch.setattr("anqa.paths.default_host_sessions_root", lambda: host)
        assert resolve_catalog_root(host) == host.resolve()

    def test_session_dir_returns_parent(self, tmp_path: Path) -> None:
        store = tmp_path / "sessions"
        session = store / "sid-1"
        session.mkdir(parents=True)
        (session / "summary.json").write_text("{}", encoding="utf-8")
        assert resolve_catalog_root(session) == store.resolve()

    def test_session_under_host_sessions(self, tmp_path: Path) -> None:
        host = tmp_path / ".grok" / "sessions"
        session = host / "%2Fproj" / "sid-1"
        session.mkdir(parents=True)
        (session / "updates.jsonl").write_text("{}\n", encoding="utf-8")
        assert resolve_catalog_root(session) == session.parent.resolve()

    def test_plain_directory(self, tmp_path: Path) -> None:
        store = tmp_path / "store"
        store.mkdir()
        assert resolve_catalog_root(store) == store.resolve()

    def test_missing_file_falls_back(self, tmp_path: Path) -> None:
        p = tmp_path / "missing.log"
        assert resolve_catalog_root(p) == default_host_sessions_root()


class TestTracesRootForReload:
    def test_session_dir_returns_parent(self, tmp_path: Path) -> None:
        sd = tmp_path / "sessions" / "sid-1"
        sd.mkdir(parents=True)
        (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
        assert traces_root_for_reload(sd) == sd.parent

    def test_store_directory(self, tmp_path: Path) -> None:
        store = tmp_path / "sessions"
        store.mkdir()
        assert traces_root_for_reload(store) == store

    def test_none_uses_host_sessions(self) -> None:
        assert traces_root_for_reload(None) == default_host_sessions_root()


class TestAppHome:
    def test_app_home_creates_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = tmp_path / "app-home"
        monkeypatch.setattr("anqa.paths.APP_HOME", fake)
        result = app_home()
        assert result == fake
        assert fake.is_dir()

    def test_cache_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = tmp_path / "app-home"
        monkeypatch.setattr("anqa.paths.APP_HOME", fake)
        result = cache_dir()
        assert result == fake / "cache"
        assert result.is_dir()

    def test_app_config_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = tmp_path / "app-home"
        monkeypatch.setattr("anqa.paths.APP_HOME", fake)
        assert app_config_path() == fake / "config.toml"

    def test_user_keys_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = tmp_path / "app-home"
        monkeypatch.setattr("anqa.paths.APP_HOME", fake)
        assert user_keys_path() == fake / "keys.toml"

    def test_user_themes_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = tmp_path / "app-home"
        monkeypatch.setattr("anqa.paths.APP_HOME", fake)
        assert user_themes_dir() == fake / "themes"

    def test_reports_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = tmp_path / "app-home"
        monkeypatch.setattr("anqa.paths.APP_HOME", fake)
        result = reports_dir()
        assert result == fake / "reports"
        assert result.is_dir()

    def test_notes_fallback_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = tmp_path / "app-home"
        monkeypatch.setattr("anqa.paths.APP_HOME", fake)
        result = notes_fallback_dir("sid")
        assert result == fake / "notes" / "sid"
        assert result.is_dir()

    def test_user_export_profiles_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = tmp_path / "app-home"
        monkeypatch.setattr("anqa.paths.APP_HOME", fake)
        result = user_export_profiles_dir()
        assert result == fake / "export_profiles"
        assert result.is_dir()
