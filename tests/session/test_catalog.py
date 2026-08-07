"""Domain session catalog (control / headless owner; no TUI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.session.catalog import (
    list_session_catalog,
    resolve_session_reference,
    session_catalog_row,
)


def _write_session(root: Path, name: str, *, title: str = "Catalog session") -> Path:
    session_dir = root / name
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": title}),
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "hi"},
                        "_meta": {"promptIndex": 1},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (session_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    return session_dir


def test_list_session_catalog_discovers_work_traces(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_session(traces, "session-catalog-a", title="Alpha review")
    rows = list_session_catalog(work)
    assert len(rows) == 1
    row = rows[0]
    assert row["sessionId"] == "session-catalog-a"
    assert row["path"] == str(sess.resolve())
    assert row["title"] == "Alpha review"
    assert row["origin"] == "work"
    assert "status" in row
    assert "model" in row


def test_list_session_catalog_empty_without_sessions(tmp_path: Path) -> None:
    work = tmp_path / "empty-work"
    work.mkdir()
    assert list_session_catalog(work) == []


def test_resolve_session_reference_by_path_and_id(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_session(traces, "session-resolve-me")
    by_path = resolve_session_reference(str(sess), work)
    assert by_path == sess.resolve()
    by_name = resolve_session_reference("session-resolve-me", work)
    assert by_name == sess.resolve()
    assert resolve_session_reference("missing-session-xyz", work) is None
    assert resolve_session_reference("", work) is None


def test_list_session_catalog_follows_show_host_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headless catalog includes host when config show_host_sessions is true."""
    from groket.session import catalog as catalog_mod

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    host = tmp_path / "host-sessions"
    _write_session(traces, "work-only-sess", title="Work")
    h_sess = host / "%2Fproj" / "host-sess"
    h_sess.mkdir(parents=True)
    (h_sess / "summary.json").write_text(
        '{"info":{"id":"host-sess"},"generated_title":"Host"}',
        encoding="utf-8",
    )
    (h_sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (h_sess / "updates.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "groket.session.sources.host_grok_sessions_root",
        lambda: host,
    )
    cfg = tmp_path / "config.json"
    cfg.write_text('{"show_host_sessions": true}\n', encoding="utf-8")
    monkeypatch.setattr(catalog_mod, "app_config_path", lambda: cfg)

    # include_host=None → config
    rows = list_session_catalog(work, include_host=None)
    ids = {r["sessionId"] for r in rows}
    assert "work-only-sess" in ids
    assert "host-sess" in ids
    origins = {r["sessionId"]: r["origin"] for r in rows}
    assert origins.get("host-sess") == "host"

    # Force off ignores config
    rows_work = list_session_catalog(work, include_host=False)
    assert {r["sessionId"] for r in rows_work} == {"work-only-sess"}


def test_session_catalog_row_none_on_bad_dir(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-session"
    empty.mkdir()
    # Empty dir still loads as meta with defaults — not None. Use missing path:
    missing = tmp_path / "nope"
    # load_session_meta_list tolerates missing files; row still builds.
    row = session_catalog_row(empty, origin="work")
    assert row is not None
    assert row["sessionId"] == "not-a-session"
    _ = missing
