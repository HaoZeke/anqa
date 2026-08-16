"""Wire timeline hydration for control-attached TUI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.session.control_views import (
    MAX_CONTENT_CHARS,
    MAX_TIMELINE_LIMIT,
    build_session_overview,
    build_session_timeline,
)
from groket.session.wire_timeline import (
    TIMELINE_RPC_CHARS,
    TIMELINE_RPC_LIMIT,
    fetch_timeline_event,
    fetch_timeline_events,
    fetch_timeline_growth,
    fetch_timeline_page,
    session_meta_from_overview,
    trace_event_from_wire,
)


def _write_session(root: Path, name: str) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": "Wire sess"}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1000,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "hello wire"},
                    }
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": 1001,
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "hi back"},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return sd


def test_trace_event_from_wire_roundtrip_fields(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "w1")
    page = build_session_timeline(sd, offset=0, limit=50, content_chars=500)
    assert page["events"]
    ev = trace_event_from_wire(page["events"][0])
    assert ev.index == page["events"][0]["index"]
    assert "user" in (ev.event_type or "").lower() or page["events"][0]["kind"] == "user"
    assert "hello" in (ev.content or "")


def test_session_meta_from_overview(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "w2")
    ov = build_session_overview(sd)
    meta = session_meta_from_overview(ov, fallback_dir=sd)
    assert meta.session_id == "w2"
    assert meta.num_events >= 1
    assert meta.session_dir == sd or meta.session_dir.name == "w2"


def test_session_meta_from_overview_uses_signals_turn_count(tmp_path: Path) -> None:
    """Attached TUI must see signals turnCount, not only the loaded tail."""
    sd = _write_session(tmp_path, "w-turns")
    (sd / "signals.json").write_text(
        json.dumps({"turnCount": 119, "toolCallCount": 2752, "primaryModelId": "grok-4.6"}),
        encoding="utf-8",
    )
    ov = build_session_overview(sd)
    assert ov["meta"]["turnCount"] == 119
    meta = session_meta_from_overview(ov, fallback_dir=sd)
    assert meta.turn_count == 119
    assert meta.tool_call_count == 2752


@pytest.mark.asyncio
async def test_fetch_timeline_events_pages(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "w3")

    class _Local:
        async def session_timeline(self, session: str, **kwargs: object) -> object:
            return build_session_timeline(
                sd,
                offset=int(kwargs.get("offset") or 0),
                limit=int(kwargs.get("limit") or 1),
                content_chars=int(kwargs.get("content_chars") or 500),
            )

    events = await fetch_timeline_events(_Local(), "w3", page_limit=1)
    assert len(events) >= 2
    assert any("hello" in (e.content or "") for e in events)


@pytest.mark.asyncio
async def test_fetch_timeline_event_uses_at_index_and_ceiling(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "w-one")
    seen: dict[str, object] = {}

    class _Local:
        async def session_timeline(self, session: str, **kwargs: object) -> object:
            seen.update(kwargs)
            return build_session_timeline(
                sd,
                offset=int(kwargs.get("offset") or 0),
                limit=int(kwargs.get("limit") or 1),
                at_index=kwargs.get("at_index")
                if isinstance(kwargs.get("at_index"), int)
                else None,
                content_chars=int(kwargs.get("content_chars") or 500),
            )

    page = build_session_timeline(sd, offset=0, limit=1, content_chars=500)
    target = int(page["events"][0]["index"])
    ev = await fetch_timeline_event(_Local(), "w-one", target)
    assert ev is not None
    assert ev.index == target
    assert seen.get("at_index") == target
    assert seen.get("content_chars") == MAX_CONTENT_CHARS


@pytest.mark.asyncio
async def test_fetch_timeline_page_then_offset_completes(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "w-page")
    offsets: list[int] = []

    class _Local:
        async def session_timeline(self, session: str, **kwargs: object) -> object:
            off = int(kwargs.get("offset") or 0)
            offsets.append(off)
            return build_session_timeline(
                sd,
                offset=off,
                limit=int(kwargs.get("limit") or 1),
                content_chars=int(kwargs.get("content_chars") or 500),
            )

    first, total = await fetch_timeline_page(_Local(), "w-page", page_limit=1)
    assert first
    assert total >= len(first)
    rest = await fetch_timeline_events(_Local(), "w-page", page_limit=1, offset=len(first))
    full = first + rest
    drained = await fetch_timeline_events(_Local(), "w-page", page_limit=1)
    assert [e.index for e in full] == [e.index for e in drained]
    assert 0 in offsets
    assert any(off > 0 for off in offsets)


@pytest.mark.asyncio
async def test_fetch_timeline_growth_requests_only_new_offset(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "w-grow")
    offsets: list[int] = []

    class _Local:
        async def session_timeline(self, session: str, **kwargs: object) -> object:
            off = int(kwargs.get("offset") or 0)
            offsets.append(off)
            return build_session_timeline(
                sd,
                offset=off,
                limit=int(kwargs.get("limit") or 1),
                content_chars=int(kwargs.get("content_chars") or 500),
            )

    held, total = await fetch_timeline_page(_Local(), "w-grow", page_limit=1)
    offsets.clear()
    grown = await fetch_timeline_growth(
        _Local(), "w-grow", held=held, new_total=max(total, len(held) + 1)
    )
    assert [e.index for e in grown[: len(held)]] == [e.index for e in held]
    assert offsets
    assert offsets[0] == len(held)
    assert 0 not in offsets


def test_timeline_rpc_pages_are_smaller_than_server_max() -> None:
    assert TIMELINE_RPC_LIMIT < MAX_TIMELINE_LIMIT
    assert TIMELINE_RPC_CHARS < MAX_CONTENT_CHARS
    assert TIMELINE_RPC_LIMIT == 200
    assert TIMELINE_RPC_CHARS == 12_000
