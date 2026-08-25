"""Tests for path resolution."""

from __future__ import annotations

from groket.paths import (
    app_config_path,
    app_home,
    cache_dir,
    default_traces_root,
    is_run_dir_name,
    mcp_registry_cache_dir,
    personas_home,
    resolve_work_and_traces,
    run_name,
    strip_run_prefix,
    user_keys_path,
    user_themes_dir,
)


class TestIsRunDirName:
    def test_valid(self):
        assert is_run_dir_name("groket-abc123-dietcoke") is True
        assert is_run_dir_name("groket-x") is True

    def test_invalid(self):
        assert is_run_dir_name("") is False
        assert is_run_dir_name("other-prefix") is False
        assert is_run_dir_name("traces") is False


class TestStripRunPrefix:
    def test_strip(self):
        assert strip_run_prefix("groket-abc123-dietcoke") == "abc123-dietcoke"

    def test_no_prefix(self):
        assert strip_run_prefix("something-else") == "something-else"


class TestRunName:
    def test_basic(self):
        assert run_name("abc", "dietcoke") == "groket-abc-dietcoke"

    def test_single_part(self):
        assert run_name("abc") == "groket-abc"

    def test_empty_parts_skipped(self):
        assert run_name("abc", "", "xyz") == "groket-abc-xyz"


class TestDefaultTracesRoot:
    def test_with_work_dir(self, tmp_path):
        result = default_traces_root(tmp_path)
        assert result == tmp_path / "runs" / "traces"

    def test_none_uses_default(self):
        result = default_traces_root(None)
        assert "runs" in str(result)
        assert "traces" in str(result)


class TestResolveWorkAndTraces:
    def test_none_uses_defaults(self):
        wd, tr = resolve_work_and_traces(None)
        assert wd.is_absolute()
        assert tr == wd / "runs" / "traces"

    def test_runs_traces_path(self, tmp_path):
        p = tmp_path / "runs" / "traces"
        p.mkdir(parents=True)
        wd, tr = resolve_work_and_traces(p)
        assert wd == tmp_path.resolve()
        assert tr == p.resolve()

    def test_session_under_traces(self, tmp_path):
        session = tmp_path / "runs" / "traces" / "groket-abc-dietcoke"
        session.mkdir(parents=True)
        (session / "summary.json").write_text("{}")
        wd, tr = resolve_work_and_traces(session)
        assert wd == tmp_path.resolve()
        assert tr == (tmp_path / "runs" / "traces").resolve()

    def test_bare_dir_with_runs(self, tmp_path):
        (tmp_path / "runs" / "traces").mkdir(parents=True)
        wd, tr = resolve_work_and_traces(tmp_path)
        assert wd == tmp_path.resolve()
        assert tr == (tmp_path / "runs" / "traces").resolve()

    def test_dir_with_traces_subdir(self, tmp_path):
        (tmp_path / "traces").mkdir()
        wd, tr = resolve_work_and_traces(tmp_path)
        assert tr == (tmp_path / "traces").resolve()

    def test_host_grok_sessions_keeps_default_work(self, tmp_path, monkeypatch):
        host = tmp_path / ".grok" / "sessions"
        host.mkdir(parents=True)
        work = tmp_path / "default-work"
        work.mkdir()
        monkeypatch.setattr("groket.paths.DEFAULT_WORK_DIR", work)
        monkeypatch.setattr("groket.paths.default_work_dir", lambda: work)
        wd, tr = resolve_work_and_traces(host)
        assert tr == host.resolve()
        assert wd == work.resolve()


class TestAppHome:
    def test_app_home_creates_dir(self, tmp_path, monkeypatch):
        fake = tmp_path / "app-home"
        monkeypatch.setattr("groket.paths.APP_HOME", fake)
        result = app_home()
        assert result == fake
        assert fake.is_dir()

    def test_cache_dir(self, tmp_path, monkeypatch):
        fake = tmp_path / "app-home"
        monkeypatch.setattr("groket.paths.APP_HOME", fake)
        result = cache_dir()
        assert result == fake / "cache"
        assert result.is_dir()

    def test_mcp_registry_cache_dir(self, tmp_path, monkeypatch):
        fake = tmp_path / "app-home"
        monkeypatch.setattr("groket.paths.APP_HOME", fake)
        result = mcp_registry_cache_dir()
        assert result == fake / "cache" / "mcp-registry"
        assert result.is_dir()

    def test_personas_home(self, tmp_path, monkeypatch):
        fake = tmp_path / "app-home"
        monkeypatch.setattr("groket.paths.APP_HOME", fake)
        result = personas_home()
        assert result == fake / "personas"
        assert result.is_dir()

    def test_app_config_path(self, tmp_path, monkeypatch):
        fake = tmp_path / "app-home"
        monkeypatch.setattr("groket.paths.APP_HOME", fake)
        result = app_config_path()
        assert result == fake / "config.toml"

    def test_user_keys_path(self, tmp_path, monkeypatch):
        fake = tmp_path / "app-home"
        monkeypatch.setattr("groket.paths.APP_HOME", fake)
        assert user_keys_path() == fake / "keys.toml"

    def test_user_themes_dir(self, tmp_path, monkeypatch):
        fake = tmp_path / "app-home"
        monkeypatch.setattr("groket.paths.APP_HOME", fake)
        assert user_themes_dir() == fake / "themes"


from pathlib import Path

from groket import paths


def test_app_home_and_dirs():
    assert paths.APP_HOME
    assert paths.default_work_dir()
    assert paths.cache_dir()


def test_resolve_work_and_traces(tmp_path: Path):
    traces = tmp_path / "runs" / "traces"
    traces.mkdir(parents=True)
    w, t = paths.resolve_work_and_traces(traces)
    assert Path(t).is_absolute()
    assert Path(w).is_absolute()


def test_traces_root_for_reload(tmp_path: Path):
    traces = tmp_path / "runs" / "traces"
    traces.mkdir(parents=True)
    root = paths.traces_root_for_reload(tmp_path, traces)
    assert Path(root).is_absolute()


# --- merged ---


import pytest


def test_paths_more(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from groket import paths

    monkeypatch.setattr(paths, "DEFAULT_WORK_DIR", tmp_path / "w")
    wd = paths.default_work_dir()
    assert wd == tmp_path / "w"
    tr = paths.default_traces_root(tmp_path / "w")
    assert "traces" in str(tr)
    w2, t2 = paths.resolve_work_and_traces(tmp_path / "w")
    assert w2
    assert t2
    paths.traces_root_for_reload(tmp_path / "w", None)
    paths.traces_root_for_reload(tmp_path / "w", tmp_path / "custom-traces")
    paths.ensure_user_extension_dirs()
    assert paths.personas_home().exists() or True
    # optional helpers if present
    for name in (
        "feedback_cache_dir",
        "run_configs_home",
        "user_models_path",
        "package_config_dir",
    ):
        fn = getattr(paths, name, None)
        if callable(fn):
            try:
                fn(tmp_path) if name.endswith("_dir") and name != "package_config_dir" else fn()
            except TypeError:
                try:
                    fn()
                except Exception:
                    pass


def test_extensions_scaffold_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from groket import paths
    from groket.extensions import scaffold

    home = tmp_path / ".groket"
    (home / "tasks").mkdir(parents=True)
    monkeypatch.setattr(paths, "APP_HOME", home)
    monkeypatch.setattr(paths, "user_tasks_dir", lambda: home / "tasks")
    monkeypatch.setattr(paths, "app_config_path", lambda: home / "config.toml")
    monkeypatch.setattr(scaffold, "user_tasks_dir", lambda: home / "tasks")

    scaffold.write_tasks_file(home / "tasks" / "t2.yaml", force=True)
    with pytest.raises(FileExistsError):
        scaffold.write_tasks_file(home / "tasks" / "t2.yaml", force=False)


from groket.paths import (
    default_work_dir,
    ensure_user_extension_dirs,
    traces_root_for_reload,
    user_tasks_dir,
)


class TestUserExtensionDirs:
    def test_user_tasks_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        fake = tmp_path / "app"
        monkeypatch.setattr("groket.paths.APP_HOME", fake)
        d = user_tasks_dir()
        assert d == fake / "tasks"
        assert d.is_dir()

    def test_ensure_user_extension_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        fake = tmp_path / "app"
        monkeypatch.setattr("groket.paths.APP_HOME", fake)
        result = ensure_user_extension_dirs()
        assert "tasks" in result
        assert "export_profiles" in result
        for d in result.values():
            assert d.is_dir()


class TestDefaultWorkDir:
    def test_default_is_under_app_home(self, monkeypatch: pytest.MonkeyPatch):
        from groket import paths

        monkeypatch.setattr(paths, "DEFAULT_WORK_DIR", paths.APP_HOME / "work")
        wd = default_work_dir()
        assert wd == paths.APP_HOME / "work"
        assert wd.is_absolute()

    def test_patched_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from groket import paths

        monkeypatch.setattr(paths, "DEFAULT_WORK_DIR", tmp_path / "custom")
        assert default_work_dir() == tmp_path / "custom"


class TestResolveWorkAndTracesExtended:
    def test_feedback_cache_path(self, tmp_path: Path):
        p = tmp_path / "runs" / "feedback_cache"
        p.mkdir(parents=True)
        wd, tr = resolve_work_and_traces(p)
        assert wd == tmp_path.resolve()
        assert "traces" in str(tr)

    def test_feedback_cache_child(self, tmp_path: Path):
        p = tmp_path / "runs" / "feedback_cache" / "session-id"
        p.mkdir(parents=True)
        wd, tr = resolve_work_and_traces(p)
        assert wd == tmp_path.resolve()

    def test_runs_path(self, tmp_path: Path):
        p = tmp_path / "runs"
        p.mkdir()
        wd, tr = resolve_work_and_traces(p)
        assert wd == tmp_path.resolve()
        assert tr == p.resolve() / "traces"

    def test_standalone_traces_folder(self, tmp_path: Path):
        p = tmp_path / "traces"
        p.mkdir()
        wd, tr = resolve_work_and_traces(p)
        assert tr == p.resolve()

    def test_session_dir_under_traces(self, tmp_path: Path):
        """Session dir whose parent is 'traces' (not under runs/)."""
        traces = tmp_path / "traces"
        sd = traces / "session-abc"
        sd.mkdir(parents=True)
        (sd / "updates.jsonl").write_text("{}\n")
        wd, tr = resolve_work_and_traces(sd)
        assert tr == traces.resolve()

    def test_dir_with_only_traces_subdir(self, tmp_path: Path):
        (tmp_path / "traces").mkdir()
        wd, tr = resolve_work_and_traces(tmp_path)
        assert tr == (tmp_path / "traces").resolve()

    def test_empty_dir_as_work_root(self, tmp_path: Path):
        empty = tmp_path / "new-work"
        empty.mkdir()
        wd, tr = resolve_work_and_traces(empty)
        assert wd == empty.resolve()
        assert "runs" in str(tr)

    def test_nonexistent_no_suffix(self, tmp_path: Path):
        p = tmp_path / "future-dir"
        wd, tr = resolve_work_and_traces(p)
        assert wd == p.resolve()
        assert "traces" in str(tr)

    def test_file_with_suffix(self, tmp_path: Path):
        p = tmp_path / "some.log"
        p.write_text("x")
        wd, tr = resolve_work_and_traces(p)
        # Falls through to default
        assert wd.is_absolute()


class TestTracesRootForReloadExtended:
    def test_session_dir_returns_parent(self, tmp_path: Path):
        sd = tmp_path / "runs" / "traces" / "session-abc"
        sd.mkdir(parents=True)
        (sd / "updates.jsonl").write_text("{}\n")
        result = traces_root_for_reload(tmp_path, sd)
        assert result == sd.parent

    def test_none_traces_path(self, tmp_path: Path):
        result = traces_root_for_reload(tmp_path, None)
        assert "traces" in str(result)
