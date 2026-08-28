"""Session catalog roots: adapter host stores."""

from __future__ import annotations

import json
from pathlib import Path

from anqa.session.sources import (
    ORIGIN_HOST,
    classify_session_origin,
    collect_session_dirs,
    host_grok_sessions_root,
    is_host_grok_sessions_root,
    is_under_host_grok_sessions,
    session_dir_for_watch_path,
    session_run_dir,
    session_scan_roots,
)


def _seed_session(root: Path, *, cwd_token: str, sid: str, title: str = "t") -> Path:
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


def test_session_run_dir_decodes_host_cwd(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path, cwd_token="%2Fmnt%2Fdev%2F_git%2Ffubar", sid="s1")
    assert session_run_dir(sess) == "/mnt/dev/_git/fubar"


def test_session_run_dir_skips_container_workspace(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path, cwd_token="%2Fworkspace", sid="s1")
    assert session_run_dir(sess) == ""


def test_session_scan_roots_host_only(tmp_path: Path) -> None:
    host = tmp_path / "host-sessions"
    host.mkdir()
    roots = session_scan_roots(include_host=True, host_root=host)
    assert len(roots) == 1
    assert roots[0].origin == ORIGIN_HOST
    assert roots[0].path == host


def test_session_scan_roots_adds_traces_path(tmp_path: Path) -> None:
    host = tmp_path / "host"
    extra = tmp_path / "extra"
    host.mkdir()
    extra.mkdir()
    roots = session_scan_roots(traces_path=extra, include_host=True, host_root=host)
    assert [r.origin for r in roots] == [ORIGIN_HOST, ORIGIN_HOST]
    assert roots[0].path == host
    assert roots[1].path == extra


def test_collect_session_dirs_union(tmp_path: Path) -> None:
    host = tmp_path / "host"
    extra = tmp_path / "extra"
    h_sess = _seed_session(host, cwd_token="%2Fproj", sid="host-sid")
    e_sess = _seed_session(extra, cwd_token="%2Fproj", sid="extra-sid")
    roots = session_scan_roots(traces_path=extra, include_host=True, host_root=host)
    found = {str(p.resolve()): o for p, o in collect_session_dirs(roots)}
    assert found[str(h_sess.resolve())] == ORIGIN_HOST
    assert found[str(e_sess.resolve())] == ORIGIN_HOST


def test_classify_and_under_host(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "sessions"
    sess = _seed_session(host, cwd_token="%2Fa", sid="s1")
    monkeypatch.setattr(
        "anqa.session.sources.host_grok_sessions_root",
        lambda: host,
    )
    assert is_under_host_grok_sessions(sess)
    assert classify_session_origin(sess, host_root=host) == ORIGIN_HOST
    other = _seed_session(tmp_path / "elsewhere", cwd_token="%2Fb", sid="o1")
    assert classify_session_origin(other, host_root=host) == ORIGIN_HOST


def test_is_host_sessions_root(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / ".grok" / "sessions"
    host.mkdir(parents=True)
    monkeypatch.setattr(
        "anqa.session.sources.host_grok_sessions_root",
        lambda: host,
    )
    assert is_host_grok_sessions_root(host)
    assert not is_host_grok_sessions_root(tmp_path)


def test_watch_path_maps_encoded_cwd_to_session_not_bucket(tmp_path: Path) -> None:
    host = tmp_path / "sessions"
    sess = _seed_session(host, cwd_token="%2FUsers%2Fali%2F_dev%2F_git%2Fanqa", sid="019abc")
    ev = sess / "updates.jsonl"
    got = session_dir_for_watch_path(ev, host)
    assert got is not None
    assert got.resolve() == sess.resolve()
    bucket = host / "%2FUsers%2Fali%2F_dev%2F_git%2Fanqa"
    assert session_dir_for_watch_path(bucket, host) is None


def test_watch_path_maps_flat_session(tmp_path: Path) -> None:
    store = tmp_path / "sessions"
    sess = store / "one"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    (sess / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    got = session_dir_for_watch_path(sess / "updates.jsonl", store)
    assert got is not None
    assert got.resolve() == sess.resolve()
