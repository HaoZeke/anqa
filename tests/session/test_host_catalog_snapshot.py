"""Host catalog snapshot: stamp gate, updates-tail status, marker fallback."""

from __future__ import annotations

import json
from pathlib import Path

from groket.parser import load_host_list_meta
from groket.session.catalog import list_session_catalog, session_catalog_row
from groket.session.mtime_export import write_host_catalog_export


def _host_session(
    root: Path,
    name: str,
    *,
    title: str,
    messages: int = 3,
    updates: str = "{}\n",
) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": name},
                "generated_title": title,
                "num_messages": messages,
            }
        ),
        encoding="utf-8",
    )
    (sd / "signals.json").write_text(
        json.dumps({"toolCallCount": 2, "turnCount": 4, "sessionDurationSeconds": 12.0}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text(updates, encoding="utf-8")
    (sd / "events.jsonl").write_text('{"type":"turn_started"}\n', encoding="utf-8")
    return sd


def _turn_completed_line() -> str:
    return json.dumps({"params": {"update": {"sessionUpdate": "turn_completed"}}}) + "\n"


def _chunk_line() -> str:
    return json.dumps({"params": {"update": {"sessionUpdate": "user_message_chunk"}}}) + "\n"


def test_host_catalog_row_skips_full_timeline_parse(tmp_path: Path, monkeypatch) -> None:
    import groket.parser as parser_mod

    sd = _host_session(
        tmp_path / "host",
        "019aaaa",
        title="Host title",
        messages=9,
        updates=_turn_completed_line(),
    )

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("host list must not parse the full timeline")

    monkeypatch.setattr(parser_mod, "_list_timeline_event_count", _boom)
    monkeypatch.setattr(parser_mod, "parse_timeline", _boom)
    row = session_catalog_row(sd, origin="host")
    assert row is not None
    assert row["title"] == "Host title"
    assert row["numEvents"] == 9
    assert row["origin"] == "host"
    assert row["toolCallCount"] == 2
    assert row["turnCount"] == 4
    assert row["status"] == "complete"


def test_host_list_meta_tail_sets_complete_vs_running(tmp_path: Path) -> None:
    host = tmp_path / "host"
    done = _host_session(host, "done-sess", title="Done", updates=_turn_completed_line())
    live = _host_session(host, "live-sess", title="Live", updates=_chunk_line())
    complete = load_host_list_meta(done)
    running = load_host_list_meta(live)
    assert complete.list_status_label() == "complete"
    assert running.list_status_label() == "running"
    done_row = session_catalog_row(done, origin="host")
    live_row = session_catalog_row(live, origin="host")
    assert done_row is not None and done_row["status"] == "complete"
    assert live_row is not None and live_row["status"] == "running"


def test_host_export_is_stamp_gated(tmp_path: Path) -> None:
    host = tmp_path / "host"
    _host_session(host, "019cccc-1111-2222-3333-444444444444", title="Host title")
    dest = tmp_path / "out" / "host.json"
    first = write_host_catalog_export(dest, host_root=host)
    assert first == dest
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["sessions"][0]["sessionId"] == "019cccc-1111-2222-3333-444444444444"
    assert payload["sessions"][0]["title"] == "Host title"
    assert payload["sessions"][0]["numEvents"] == 3
    assert "stamps" in payload
    mtime1 = dest.stat().st_mtime
    second = write_host_catalog_export(dest, host_root=host)
    assert second == dest
    assert dest.stat().st_mtime == mtime1


def test_host_export_rebuilds_when_stamps_unreadable(tmp_path: Path) -> None:
    host = tmp_path / "host"
    _host_session(host, "019eeee-1111-2222-3333-444444444444", title="Rebuild")
    dest = tmp_path / "out" / "host.json"
    write_host_catalog_export(dest, host_root=host)
    dest.write_text("{not-json\n", encoding="utf-8")
    write_host_catalog_export(dest, host_root=host)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["sessions"][0]["title"] == "Rebuild"

    dest.write_text(json.dumps({"stamps": "nope", "sessions": []}), encoding="utf-8")
    write_host_catalog_export(dest, host_root=host)
    assert json.loads(dest.read_text(encoding="utf-8"))["sessions"][0]["title"] == "Rebuild"

    dest.write_text(json.dumps({"stamps": [["only-path"]], "sessions": []}), encoding="utf-8")
    write_host_catalog_export(dest, host_root=host)
    assert json.loads(dest.read_text(encoding="utf-8"))["sessions"][0]["title"] == "Rebuild"

    dest.write_text(
        json.dumps({"stamps": [["/x", True, 1, 2]], "sessions": []}),
        encoding="utf-8",
    )
    write_host_catalog_export(dest, host_root=host)
    assert json.loads(dest.read_text(encoding="utf-8"))["sessions"][0]["title"] == "Rebuild"


def test_list_session_catalog_stamp_hit_skips_session_files(tmp_path: Path, monkeypatch) -> None:
    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    host = tmp_path / "host"
    _host_session(host, "019dddd-1111-2222-3333-444444444444", title="Snap")
    dest = tmp_path / "snap.json"
    rows1 = list_session_catalog(work, include_host=True, host_root=host, host_catalog_cache=dest)
    assert rows1[0]["sessionId"] == "019dddd-1111-2222-3333-444444444444"
    mtime1 = dest.stat().st_mtime

    opened: list[str] = []
    real_open = Path.open

    def track_open(self: Path, *args: object, **kwargs: object) -> object:
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_open)
    rows2 = list_session_catalog(work, include_host=True, host_root=host, host_catalog_cache=dest)
    assert rows2[0]["title"] == "Snap"
    assert dest.stat().st_mtime == mtime1
    assert not any(name.endswith("summary.json") for name in opened)
    assert not any(name.endswith("signals.json") for name in opened)
    assert not any(name.endswith("updates.jsonl") for name in opened)
    assert not any(name.endswith("events.jsonl") for name in opened)


def test_list_session_catalog_events_growth_does_not_open_events(
    tmp_path: Path, monkeypatch
) -> None:
    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    host = tmp_path / "host"
    sd = _host_session(host, "grow-ev", title="Grow")
    dest = tmp_path / "snap.json"
    list_session_catalog(work, include_host=True, host_root=host, host_catalog_cache=dest)
    (sd / "events.jsonl").write_text('{"type":"turn_started"}\n' * 20, encoding="utf-8")

    opened: list[str] = []
    real_open = Path.open

    def track_open(self: Path, *args: object, **kwargs: object) -> object:
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_open)
    list_session_catalog(work, include_host=True, host_root=host, host_catalog_cache=dest)
    assert not any(name.endswith("events.jsonl") for name in opened)
