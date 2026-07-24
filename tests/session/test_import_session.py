"""Import native ~/.grok sessions into a work traces tree."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.session.import_session import (
    IMPORT_KIND,
    IMPORT_META_NAME,
    IMPORTED_DIRNAME,
    HostSessionRow,
    host_grok_sessions_root,
    import_session,
    is_session_directory,
    list_host_grok_sessions,
    match_host_session,
)


def _seed_host_session(
    root: Path,
    *,
    cwd_token: str = "%2Fhome%2Fali%2Fproj",
    sid: str = "019f-import-test-sid",
    title: str = "Import me",
) -> Path:
    """Native layout: ``root/<cwd-token>/<sid>/summary.json`` (+ events)."""
    sess = root / cwd_token / sid
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text(
        json.dumps({"session_id": sid, "generated_title": title}),
        encoding="utf-8",
    )
    (sess / "events.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    (sess / "updates.jsonl").write_text("", encoding="utf-8")
    return sess


def test_host_grok_sessions_root_default() -> None:
    assert host_grok_sessions_root() == Path.home() / ".grok" / "sessions"


def test_is_session_directory_markers(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not is_session_directory(empty)
    assert not is_session_directory(tmp_path / "missing")

    with_summary = tmp_path / "s1"
    with_summary.mkdir()
    (with_summary / "summary.json").write_text("{}", encoding="utf-8")
    assert is_session_directory(with_summary)

    with_events = tmp_path / "s2"
    with_events.mkdir()
    (with_events / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert is_session_directory(with_events)

    empty_events = tmp_path / "s3"
    empty_events.mkdir()
    (empty_events / "events.jsonl").write_text("", encoding="utf-8")
    assert not is_session_directory(empty_events)

    with_chat = tmp_path / "s4"
    with_chat.mkdir()
    (with_chat / "chat_history.jsonl").write_text("{}\n", encoding="utf-8")
    assert is_session_directory(with_chat)


def test_list_host_grok_sessions_sorted_and_capped(tmp_path: Path) -> None:
    import os
    import time

    a = _seed_host_session(tmp_path, sid="sid-a", title="Alpha")
    b = _seed_host_session(
        tmp_path,
        cwd_token="%2Fother",
        sid="sid-b",
        title="Beta",
    )
    # Explicit mtimes so sort order is deterministic (same-second FS clocks).
    old = time.time() - 3600
    new = time.time()
    for p in (a, *a.iterdir()):
        os.utime(p, (old, old))
    for p in (b, *b.iterdir()):
        os.utime(p, (new, new))

    rows = list_host_grok_sessions(tmp_path, limit=1)
    assert len(rows) == 1
    assert rows[0].session_id == "sid-b"
    assert rows[0].title == "Beta"
    assert "other" in rows[0].cwd_label or rows[0].cwd_label.startswith("/")

    all_rows = list_host_grok_sessions(tmp_path, limit=0)
    assert {r.session_id for r in all_rows} == {"sid-a", "sid-b"}
    assert a in {r.path for r in all_rows}


def test_list_host_grok_sessions_reads_summary_only(tmp_path: Path, monkeypatch) -> None:
    """Titles come from summary.json; full parser meta is not required."""
    _seed_host_session(tmp_path, sid="sid-light", title="Light title")
    # Corrupt/missing heavy side channels still list fine (list does not open them).
    sess = next(tmp_path.rglob("summary.json")).parent
    (sess / "signals.json").write_text("{not-json", encoding="utf-8")
    (sess / "events.jsonl").write_text("x" * 200_000, encoding="utf-8")

    calls: list[object] = []

    def _track(*a, **k):
        calls.append((a, k))
        raise AssertionError("load_session_meta should not be used for listing")

    monkeypatch.setattr("groket.parser.load_session_meta", _track)
    rows = list_host_grok_sessions(tmp_path, limit=0)
    assert calls == []
    assert len(rows) == 1
    assert rows[0].title == "Light title"
    assert "Light" in rows[0].search_text()


def test_list_host_grok_sessions_default_limit_is_uncapped(tmp_path: Path) -> None:
    for i in range(3):
        _seed_host_session(tmp_path, cwd_token=f"%2Fcwd{i}", sid=f"sid-{i}", title=f"T{i}")
    rows = list_host_grok_sessions(tmp_path)
    assert len(rows) == 3


def test_match_host_session_title_and_path() -> None:
    row = HostSessionRow(
        path=Path("/home/ali/.grok/sessions/%2Fhome%2Fali%2Fgrok-trace-eval/sid-1"),
        session_id="sid-1",
        title="Notes Feature Review",
        cwd_label="/home/ali/grok-trace-eval",
        mtime=1.0,
    )
    assert match_host_session("", row) > 0
    assert match_host_session("Notes", row) > 0
    assert match_host_session("feature review", row) > 0  # multi-word, any order via tokens
    assert match_host_session("review feature", row) > 0
    assert match_host_session("grok-trace-eval", row) > 0
    assert match_host_session("trace-eval", row) > 0
    assert match_host_session("coredis", row) == 0
    assert match_host_session("sid-1", row) > 0
    # Encoded path parent still searchable via segments.
    assert match_host_session("%2Fhome", row) > 0 or match_host_session("home", row) > 0


def test_import_session_copy(tmp_path: Path) -> None:
    host = tmp_path / "host"
    traces = tmp_path / "traces"
    src = _seed_host_session(host, sid="copy-sid", title="Copy me")

    result = import_session(src, traces_root=traces)
    assert result.session_id == "copy-sid"
    assert not result.linked
    assert result.dest.is_dir()
    assert not result.dest.is_symlink()
    assert (result.dest / "summary.json").is_file()
    assert (result.dest / "events.jsonl").read_text(encoding="utf-8")
    meta = json.loads((result.dest / IMPORT_META_NAME).read_text(encoding="utf-8"))
    assert meta["kind"] == IMPORT_KIND
    assert meta["source"] == str(src.resolve())
    assert meta["linked"] is False
    assert IMPORTED_DIRNAME in result.dest.parts
    # Source unchanged.
    assert (src / "summary.json").is_file()


def test_import_session_link(tmp_path: Path) -> None:
    host = tmp_path / "host"
    traces = tmp_path / "traces"
    src = _seed_host_session(host, sid="link-sid")

    result = import_session(src, traces_root=traces, link=True)
    assert result.linked
    assert result.dest.is_symlink()
    assert result.dest.resolve() == src.resolve()
    # Meta lives beside the link, not inside the host session.
    side = result.dest.parent / f"{result.dest.name}.{IMPORT_META_NAME}"
    assert side.is_file()
    assert not (src / IMPORT_META_NAME).exists()
    meta = json.loads(side.read_text(encoding="utf-8"))
    assert meta["linked"] is True


def test_import_session_force_replaces(tmp_path: Path) -> None:
    host = tmp_path / "host"
    traces = tmp_path / "traces"
    src = _seed_host_session(host, sid="force-sid")
    first = import_session(src, traces_root=traces)
    (first.dest / "marker.txt").write_text("old", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        import_session(src, traces_root=traces)

    second = import_session(src, traces_root=traces, force=True)
    assert second.dest == first.dest
    assert not (second.dest / "marker.txt").exists()
    assert (second.dest / "summary.json").is_file()


def test_import_session_rejects_non_session(tmp_path: Path) -> None:
    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / "readme.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError, match="not a Grok session"):
        import_session(junk, traces_root=tmp_path / "traces")


def test_import_session_missing_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        import_session(tmp_path / "nope", traces_root=tmp_path / "traces")
