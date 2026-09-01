"""Session-discovery contract (same results with or without anqa._scan)."""

from __future__ import annotations

from pathlib import Path

import pytest
from anqa.scan import (
    WALK_SKIP_DIRS,
    find_files,
    find_sessions,
    looks_like_session_dir,
    scan_forced_off,
    skip_dir_name,
    using_scan,
)


def _make_tree(tmp_path: Path) -> Path:
    root = tmp_path / "traces"
    sess = root / "sess"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    (sess / "workspace" / "fake").mkdir(parents=True)
    (sess / "workspace" / "fake" / "summary.json").write_text("{}", encoding="utf-8")

    junk = root / "workspace" / "junk"
    junk.mkdir(parents=True)
    (junk / "summary.json").write_text("{}", encoding="utf-8")

    plugins = root / "anqa-plugins" / "x"
    plugins.mkdir(parents=True)
    (plugins / "summary.json").write_text("{}", encoding="utf-8")

    stage = root / "foo.stage" / "inner"
    stage.mkdir(parents=True)
    (stage / "summary.json").write_text("{}", encoding="utf-8")

    seed = root / ".anqa-resume-seed" / "seed"
    seed.mkdir(parents=True)
    (seed / "summary.json").write_text("{}", encoding="utf-8")

    child = root / "keep" / "child-sess"
    child.mkdir(parents=True)
    (child / "events.jsonl").write_text("{}\n", encoding="utf-8")

    empty = root / "empty-ev"
    empty.mkdir(parents=True)
    (empty / "events.jsonl").write_text("", encoding="utf-8")

    nested = root / "parent" / "subagents" / "nested"
    nested.mkdir(parents=True)
    (root / "parent" / "summary.json").write_text("{}", encoding="utf-8")
    (nested / "summary.json").write_text("{}", encoding="utf-8")
    return root


def test_walk_skip_dirs() -> None:
    assert "workspace" in WALK_SKIP_DIRS
    assert skip_dir_name("workspace")
    assert skip_dir_name("foo.stage")
    assert not skip_dir_name("keep")


def test_looks_like_summary_json(tmp_path: Path) -> None:
    sd = tmp_path / "with-summary"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    assert looks_like_session_dir(sd) is True


def test_looks_like_updates_jsonl(tmp_path: Path) -> None:
    sd = tmp_path / "with-updates"
    sd.mkdir()
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    assert looks_like_session_dir(sd) is True


def test_looks_like_empty_events_false(tmp_path: Path) -> None:
    sd = tmp_path / "empty-events"
    sd.mkdir()
    (sd / "events.jsonl").write_text("", encoding="utf-8")
    assert looks_like_session_dir(sd) is False


def test_looks_like_nonempty_events_true(tmp_path: Path) -> None:
    sd = tmp_path / "live-events"
    sd.mkdir()
    (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert looks_like_session_dir(sd) is True


def test_looks_like_missing_false(tmp_path: Path) -> None:
    assert looks_like_session_dir(tmp_path / "missing") is False


def test_looks_like_dangling_events_symlink(tmp_path: Path) -> None:
    sd = tmp_path / "dangling"
    sd.mkdir()
    (sd / "events.jsonl").symlink_to(tmp_path / "missing-target")
    assert looks_like_session_dir(sd) is False


def test_find_files_skips_workspace_and_matches_suffix(tmp_path: Path) -> None:
    root = tmp_path / "store"
    (root / "keep").mkdir(parents=True)
    (root / "workspace").mkdir()
    (root / "keep" / "session-a.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "workspace" / "hidden.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "keep" / "other.txt").write_text("x", encoding="utf-8")
    found = {p.name for p in find_files(root, suffix=".jsonl", name_prefix="session-")}
    assert found == {"session-a.jsonl"}


def test_find_sessions_skips_workspace_and_finds_sessions(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    found = {p.relative_to(root).as_posix() for p in find_sessions(root)}
    assert found == {"sess", "keep/child-sess", "parent"}


def test_scan_env_is_read_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANQA_SCAN", "0")
    assert using_scan() is False
    monkeypatch.setenv("ANQA_SCAN", "1")
    try:
        from anqa import _core as ext
    except ImportError:
        assert using_scan() is False
    else:
        assert using_scan() is True
        assert ext.find_sessions.__name__ == "find_sessions"


def test_scan_env_matches_using_scan() -> None:
    if scan_forced_off():
        assert using_scan() is False
    else:
        try:
            from anqa import _core as ext
        except ImportError:
            assert using_scan() is False
        else:
            assert using_scan() is True
            assert ext.find_sessions.__name__ == "find_sessions"


def test_scan_on_requires_compiled_module() -> None:
    if scan_forced_off():
        pytest.skip("ANQA_SCAN disables the compiled module")
    import anqa._core as ext

    assert using_scan() is True
    assert callable(ext.keep_updates_line)
