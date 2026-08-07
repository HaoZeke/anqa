"""Domain control views: session/get, timeline, turns, usage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.session.control_views import (
    build_session_findings,
    build_session_get,
    build_session_overview,
    build_session_timeline,
    build_session_turns,
    build_session_usage,
)


def _write_session(root: Path, name: str) -> Path:
    session_dir = root / name
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": name},
                "generated_title": "View session",
                "model": "grok-test",
            }
        ),
        encoding="utf-8",
    )
    updates = [
        {
            "timestamp": 1000,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "hello user"},
                    "_meta": {"promptIndex": 2},
                }
            },
        },
        {
            "timestamp": 1001,
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hello agent"},
                }
            },
        },
        {
            "timestamp": 1002,
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "c1",
                    "title": "read_file",
                    "kind": "read",
                    "status": "completed",
                    "rawInput": {"target_file": "/tmp/x"},
                }
            },
        },
    ]
    (session_dir / "updates.jsonl").write_text(
        "".join(json.dumps(u) + "\n" for u in updates),
        encoding="utf-8",
    )
    (session_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    return session_dir


def test_build_session_get_meta(tmp_path: Path) -> None:
    sd = _write_session(tmp_path / "runs" / "traces", "sess-get")
    got = build_session_get(sd)
    assert got["sessionId"] == "sess-get"
    assert got["title"] == "View session"
    assert "status" in got
    assert got["path"]
    assert "notesRevision" in got
    assert "numEvents" in got


def test_build_session_timeline_pages(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "sess-tl")
    full = build_session_timeline(sd, offset=0, limit=10)
    assert full["total"] >= 1
    assert full["events"]
    assert "content" in full["events"][0]
    assert "type" in full["events"][0]
    page = build_session_timeline(sd, offset=0, limit=1)
    assert len(page["events"]) == 1
    assert page["limit"] == 1
    # Offset advances through the same ordered list (HUD scroll/fill).
    if full["total"] >= 2:
        second = build_session_timeline(sd, offset=1, limit=1)
        assert len(second["events"]) == 1
        assert second["offset"] == 1
        assert second["events"][0]["index"] != page["events"][0]["index"]
    short = build_session_timeline(sd, offset=0, limit=50, content_chars=4)
    for ev in short["events"]:
        body = str(ev.get("content") or "")
        if int(ev.get("contentLength") or 0) > 4:
            assert len(body) <= 4
            assert ev.get("contentTruncated") is True


def test_build_session_turns(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "sess-turns")
    turns = build_session_turns(sd)
    assert turns["sessionId"] == "sess-turns"
    assert turns["total"] >= 1
    assert turns["turns"]
    row = turns["turns"][0]
    assert "eventCount" in row
    assert row.get("summary") == "hello user"
    assert row.get("userEventIndex") is not None


def test_build_session_usage(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "sess-usage")
    usage = build_session_usage(sd)
    assert usage["sessionId"] == "sess-usage"
    assert "hostTools" in usage
    assert "mcpServers" in usage


def test_build_session_overview_one_shot(tmp_path: Path) -> None:
    from groket.session.control_views import build_session_overview

    sd = _write_session(tmp_path, "sess-ov")
    ov = build_session_overview(sd, timeline_limit=20, content_chars=200)
    assert ov["sessionId"] == "sess-ov"
    assert "meta" in ov
    assert ov["turns"]["total"] >= 1
    assert ov["turns"]["turns"]
    t0 = ov["turns"]["turns"][0]
    assert "eventCount" in t0
    assert t0.get("summary") == "hello user"
    # Timeline is lazy: total only; clients call session/timeline for rows.
    assert ov["timeline"]["total"] >= 1
    assert ov["timeline"]["events"] == []
    assert ov["timeline"].get("lazy") is True
    assert "notes" in ov
    assert "findings" in ov
    assert ov["findings"]["total"] == 0
    page = build_session_timeline(sd, offset=0, limit=50)
    assert page["events"]
    kinds = {e.get("kind") for e in page["events"]}
    assert kinds & {"user", "agent", "tool", "other", "thought", "session"}
    for e in page["events"]:
        assert "heading" in e
        assert "kind" in e


def test_build_session_findings_maps_events_to_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Findings from analysis cache get sequential turnIndices via eventIndices."""
    sd = _write_session(tmp_path, "sess-find")
    page = build_session_timeline(sd, offset=0, limit=50)
    assert page["events"]
    # Pick a real event index from the parsed timeline.
    ev_idx = int(page["events"][0]["index"])

    cache_root = tmp_path / "cache"
    plugin_dir = cache_root / "analysis" / sd.name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "engine.json").write_text(
        json.dumps(
            {
                "_schema": 1,
                "_plugin_version": "0",
                "_trace_mtime": 0,
                "result": {
                    "analyzer_id": "engine",
                    "findings": [
                        {
                            "id": "f1",
                            "plugin_id": "engine",
                            "severity": "high",
                            "title": "Linked finding",
                            "detail": "points at an event",
                            "category": "test",
                            "event_indices": [ev_idx],
                            "update_indices": [],
                            "tool_call_ids": [],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "groket.paths.analysis_cache_dir",
        lambda: cache_root,
    )
    found = build_session_findings(sd)
    assert found["total"] == 1
    row = found["findings"][0]
    assert row["title"] == "Linked finding"
    assert row["eventIndices"] == [ev_idx]
    assert row["primaryEventIndex"] == ev_idx
    assert isinstance(row["turnIndices"], list)
    assert row["turnIndices"]  # resolved into at least one turn
    assert row["primaryTurnIndex"] == row["turnIndices"][0]

    ov = build_session_overview(sd)
    assert ov["findings"]["total"] == 1
    assert ov["findings"]["findings"][0]["primaryTurnIndex"] is not None


def test_timeline_event_kind_and_tool_family() -> None:
    from groket.models import TraceEvent
    from groket.session.control_views import timeline_event_mapping, tool_family

    assert tool_family("read_file") == "read"
    assert tool_family("run_terminal_command") == "shell"
    assert tool_family("foo__bar") == "mcp"
    ev = TraceEvent(
        index=1,
        event_type="tool_call",
        content="",
        tool_name="read_file",
        raw_input={"target_file": "/tmp/x"},
    )
    m = timeline_event_mapping(ev)
    assert m["kind"] == "tool"
    assert m["toolFamily"] == "read"
    assert m["heading"]


def test_timeline_system_reminder_not_labeled_user() -> None:
    """Harness user_message_chunk chrome must not paint as operator User."""
    from groket.models import TraceEvent
    from groket.session.control_views import timeline_event_mapping

    bg = (
        "<system-reminder>\nBackground task "
        '"call-0001a5e8-7301-4869-8c16-deaadffea580-51" completed (exit code: 0).\n'
        "Command: /bin/chmod +x /tmp/x\n"
        "Use get_command_or_subagent_output(...) to see the full output.\n"
        "</system-reminder>"
    )
    m = timeline_event_mapping(TraceEvent(index=252, event_type="user_message_chunk", content=bg))
    assert m["kind"] == "system"
    assert m["heading"] == "Background task"
    assert m["harnessChrome"] is True
    assert m["heading"] != "User"
    assert "<system-reminder>" not in str(m["content"])
    assert "Background task" in str(m["content"])
    assert "chmod" in str(m["content"])

    skills = (
        "<system-reminder>\nThe following skills are available for use:\n"
        "- check-work\n</system-reminder>"
    )
    m2 = timeline_event_mapping(
        TraceEvent(index=3, event_type="user_message_chunk", content=skills)
    )
    assert m2["kind"] == "system"
    assert m2["heading"] == "System reminder"

    real = timeline_event_mapping(
        TraceEvent(index=4, event_type="user_message_chunk", content="please fix the flaky test")
    )
    assert real["kind"] == "user"
    assert real["heading"] == "User"
    assert real["harnessChrome"] is False
