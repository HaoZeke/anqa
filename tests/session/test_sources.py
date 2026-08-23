"""Session catalog roots: work traces + optional host Grok."""

from __future__ import annotations

import json
from pathlib import Path

from groket.session.sources import (
    ORIGIN_HOST,
    ORIGIN_WORK,
    classify_session_origin,
    collect_session_dirs,
    host_grok_sessions_root,
    is_host_grok_sessions_root,
    is_under_host_grok_sessions,
    session_dir_for_watch_path,
    session_run_dir,
    session_scan_roots,
    work_traces_root,
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


def test_work_traces_root(tmp_path: Path) -> None:
    assert work_traces_root(tmp_path) == tmp_path / "runs" / "traces"


def test_session_run_dir_decodes_host_cwd(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path, cwd_token="%2Fmnt%2Fdev%2F_git%2Ffubar", sid="s1")
    assert session_run_dir(sess) == "/mnt/dev/_git/fubar"


def test_session_run_dir_skips_container_workspace(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path, cwd_token="%2Fworkspace", sid="s1")
    assert session_run_dir(sess) == ""


def test_session_run_dir_uses_repo_path_when_cwd_is_workspace(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path, cwd_token="%2Fworkspace", sid="s1")
    (sess / "run.json").write_text(
        json.dumps({"repo_path": str(tmp_path / "proj")}),
        encoding="utf-8",
    )
    assert session_run_dir(sess) == str((tmp_path / "proj").expanduser())


def test_session_scan_roots_work_only(tmp_path: Path) -> None:
    roots = session_scan_roots(tmp_path, include_host=False)
    assert len(roots) == 1
    assert roots[0].origin == ORIGIN_WORK
    assert roots[0].path == tmp_path / "runs" / "traces"


def test_session_scan_roots_with_host(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "host-sessions"
    host.mkdir()
    monkeypatch.setattr(
        "groket.session.sources.host_grok_sessions_root",
        lambda: host,
    )
    roots = session_scan_roots(tmp_path, include_host=True)
    assert [r.origin for r in roots] == [ORIGIN_WORK, ORIGIN_HOST]
    assert roots[1].path == host


def test_collect_session_dirs_union(tmp_path: Path) -> None:
    work = tmp_path / "runs" / "traces"
    host = tmp_path / "host"
    w_sess = _seed_session(work / "groket-run-1", cwd_token="%2Fworkspace", sid="work-sid")
    h_sess = _seed_session(host, cwd_token="%2Fproj", sid="host-sid")
    roots = session_scan_roots(tmp_path, include_host=True, host_root=host)
    found = {str(p.resolve()): o for p, o in collect_session_dirs(roots)}
    assert found[str(w_sess.resolve())] == ORIGIN_WORK
    assert found[str(h_sess.resolve())] == ORIGIN_HOST


def test_classify_and_under_host(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "sessions"
    sess = _seed_session(host, cwd_token="%2Fa", sid="s1")
    monkeypatch.setattr(
        "groket.session.sources.host_grok_sessions_root",
        lambda: host,
    )
    assert is_under_host_grok_sessions(sess)
    assert (
        classify_session_origin(sess, work_traces=tmp_path / "runs" / "traces", host_root=host)
        == ORIGIN_HOST
    )
    work_sess = _seed_session(
        tmp_path / "runs" / "traces" / "groket-x", cwd_token="%2Fworkspace", sid="w1"
    )
    assert (
        classify_session_origin(work_sess, work_traces=tmp_path / "runs" / "traces", host_root=host)
        == ORIGIN_WORK
    )


def test_is_host_sessions_root(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / ".grok" / "sessions"
    host.mkdir(parents=True)
    monkeypatch.setattr(
        "groket.session.sources.host_grok_sessions_root",
        lambda: host,
    )
    assert is_host_grok_sessions_root(host)
    assert not is_host_grok_sessions_root(tmp_path)


def test_watch_path_maps_encoded_cwd_to_session_not_bucket(tmp_path: Path) -> None:
    host = tmp_path / "sessions"
    sess = _seed_session(host, cwd_token="%2FUsers%2Fali%2F_dev%2F_git%2Fgroket", sid="019abc")
    ev = sess / "updates.jsonl"
    got = session_dir_for_watch_path(ev, host)
    assert got is not None
    assert got.resolve() == sess.resolve()
    bucket = host / "%2FUsers%2Fali%2F_dev%2F_git%2Fgroket"
    assert session_dir_for_watch_path(bucket, host) is None


def test_watch_path_maps_flat_eval_session(tmp_path: Path) -> None:
    traces = tmp_path / "runs" / "traces"
    sess = traces / "one"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    (sess / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    got = session_dir_for_watch_path(sess / "updates.jsonl", traces)
    assert got is not None
    assert got.resolve() == sess.resolve()
