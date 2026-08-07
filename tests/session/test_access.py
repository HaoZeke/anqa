"""LocalSessionAccess domain façade (in-process, no control socket)."""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.session.access import LocalSessionAccess, filter_session_catalog


def test_filter_session_catalog_query_and_limit() -> None:
    rows = [
        {
            "sessionId": "a",
            "path": "/tmp/a",
            "title": "Alpha Rocket",
            "label": "",
            "model": "grok",
            "status": "complete",
            "outcome": "",
            "origin": "eval",
        },
        {
            "sessionId": "b",
            "path": "/tmp/b",
            "title": "Host session",
            "label": "",
            "model": "grok",
            "status": "running",
            "outcome": "",
            "origin": "host",
        },
    ]
    full = filter_session_catalog(rows)
    assert full["total"] == 2
    assert full["matched"] == 2
    assert len(full["sessions"]) == 2

    host_only = filter_session_catalog(rows, query="host")
    assert host_only["matched"] == 1
    assert host_only["sessions"][0]["sessionId"] == "b"

    casefold = filter_session_catalog(rows, query="ROCKET")
    assert casefold["matched"] == 1
    assert casefold["sessions"][0]["sessionId"] == "a"

    limited = filter_session_catalog(rows, limit=1)
    assert len(limited["sessions"]) == 1
    assert limited["matched"] == 2


def test_local_access_list_and_missing_session(tmp_path: Path) -> None:
    session = tmp_path / "sess-one"
    session.mkdir()
    (session / "signals.json").write_text("{}", encoding="utf-8")

    def resolve(ref: str) -> Path | None:
        if ref in {session.name, str(session)}:
            return session
        p = Path(ref)
        return p if p.is_dir() else None

    access = LocalSessionAccess(
        resolve_session=resolve,
        list_sessions=lambda: [
            {
                "sessionId": session.name,
                "path": str(session),
                "title": "One",
                "origin": "eval",
            }
        ],
        work_dir=tmp_path,
    )
    listed = access.list_sessions(query="one")
    assert listed["matched"] == 1
    assert listed["sessions"][0]["sessionId"] == session.name

    with pytest.raises(FileNotFoundError):
        access.session_get("missing-id")

    got = access.session_get(session.name)
    assert got.get("sessionId") == session.name or "path" in got
