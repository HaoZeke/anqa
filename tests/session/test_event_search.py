"""Anqa event search store: luqum tree against indexed TraceEvent rows."""

from __future__ import annotations

from pathlib import Path

import pytest
from anqa.models import TraceEvent
from anqa.session.control_views import build_session_timeline
from anqa.session.event_search import (
    TimelineStamp,
    ensure_indexed,
    index_covers,
    index_stats,
    matching_indexes,
    reset_indexes,
    scan_index,
)
from anqa.session.query import event_matches_query

_STAMP: TimelineStamp = (1.0, 1, 0, 0)


@pytest.fixture(autouse=True)
def _clear_search_index() -> None:
    reset_indexes()
    yield
    reset_indexes()


def _events() -> list[TraceEvent]:
    return [
        TraceEvent(
            index=1,
            event_type="tool_call",
            tool_name="read_file",
            content="hello user",
            is_error=True,
        ),
        TraceEvent(
            index=2,
            event_type="user_message_chunk",
            content="please fix hello",
        ),
        TraceEvent(
            index=3,
            event_type="tool_call",
            tool_name="grep",
            content="needle-token in a large body " + ("x" * 4000),
        ),
    ]


def _turns() -> dict[int, int]:
    return {1: 2, 2: 0, 3: 4}


def _hits(
    events: list[TraceEvent],
    query: str,
    *,
    key: str = "sess",
    stamp: TimelineStamp = _STAMP,
    turns: dict[int, int] | None = None,
) -> list[int]:
    return matching_indexes(
        events,
        query,
        key=key,
        stamp=stamp,
        turns=turns if turns is not None else _turns(),
    )


def _spec_hits(events: list[TraceEvent], query: str) -> list[int]:
    turns = _turns()
    return [
        int(ev.index)
        for ev in events
        if event_matches_query(ev, query, turn=turns.get(int(ev.index)))
    ]


@pytest.mark.parametrize(
    "query",
    [
        "hello",
        "has:error",
        "errors:>=1",
        "is:tool AND has:error",
        "tool:read",
        "tool:read_file",
        "tool:grep",
        "turn:2",
        "turn:>=2",
        "turn:3",
        "is:user",
        "is:workflow",
        "has:error:>=1",
        "user:hello",
        "is:user AND user:fix",
        "hello AND has:error",
        "has:error OR is:user",
        "hello AND NOT has:error",
        "needle-token",
    ],
)
def test_store_matches_event_query_language(query: str) -> None:
    events = _events()
    assert _hits(events, query) == _spec_hits(events, query)


def _timed_events() -> list[TraceEvent]:
    return [
        TraceEvent(
            index=0,
            event_type="user_message_chunk",
            content="hi",
            timestamp=1000,
        ),
        TraceEvent(
            index=1,
            event_type="tool_call",
            tool_name="read_file",
            tool_call_id="c1",
            timestamp=1001,
        ),
        TraceEvent(
            index=2,
            event_type="tool_call_update",
            tool_name="read_file",
            tool_call_id="c1",
            timestamp=1003,
        ),
        TraceEvent(
            index=3,
            event_type="agent_message_chunk",
            content="done",
            timestamp=1005,
        ),
    ]


def test_timeline_duration_is_pair_seconds() -> None:
    from anqa.session.event_search import event_durations

    events = _timed_events()
    durs = event_durations(events)
    assert durs[0] == 1
    assert durs[1] == 2
    assert 2 not in durs
    assert event_matches_query(events[1], "duration:>=2", duration_seconds=int(durs[1]))
    assert not event_matches_query(events[1], "duration:>=2", duration_seconds=0)
    assert _hits(events, "duration:>=2") == [1]
    assert _hits(events, "duration:>=1") == [0, 1]
    assert _hits(events, "duration:<2") == [0]
    assert _hits(events, "is:tool AND duration:>=2") == [1]


def test_empty_query_returns_every_index() -> None:
    events = _events()
    assert _hits(events, "") == [1, 2, 3]
    assert _hits(events, "   ") == [1, 2, 3]


def test_same_stamp_reuses_rows() -> None:
    events = _events()
    first = _hits(events, "hello", key="reuse", stamp=(2.0, 10, 0, 0))
    second = _hits(events, "has:error", key="reuse", stamp=(2.0, 10, 0, 0))
    assert first == _spec_hits(events, "hello")
    assert second == _spec_hits(events, "has:error")
    grown = [*events, TraceEvent(index=4, event_type="assistant_message", content="brand new")]
    later = _hits(grown, "brand", key="reuse", stamp=(3.0, 20, 0, 0))
    assert later == [4]


def test_scan_index_matches_warm_store() -> None:
    events = _events()
    key = "scan-warm"
    stamp: TimelineStamp = (8.0, 3, 0, 0)
    ensure_indexed(events, key=key, stamp=stamp, turns=_turns())
    assert index_covers(key, stamp, len(events), hay=True)
    assert scan_index(key, "hello") == matching_indexes(
        events, "hello", key=key, stamp=stamp, turns=_turns()
    )
    assert scan_index(key, "has:error") == _spec_hits(events, "has:error")


def test_ensure_indexed_then_search() -> None:
    events = _events()
    ensure_indexed(events, key="warm", stamp=(4.0, 1, 0, 0), turns=_turns())
    assert matching_indexes(
        events,
        "has:error",
        key="warm",
        stamp=(4.0, 1, 0, 0),
        turns=_turns(),
    ) == _spec_hits(events, "has:error")


def test_short_needle_still_matches() -> None:
    events = [TraceEvent(index=1, event_type="user_message_chunk", content="ok")]
    assert _hits(events, "ok", turns={}) == [1]
    assert _hits(events, "zz", turns={}) == []


def test_matching_indexes_refills_hay_when_body_changes() -> None:
    """Same stamp and length still pick up a streamed / edited body."""
    ev = TraceEvent(index=1, event_type="user_message_chunk", content="hello")
    events = [ev]
    stamp: TimelineStamp = (1.0, 1, 0, 0)
    key = "mutate-body"
    assert matching_indexes(events, "hello", key=key, stamp=stamp, turns={}) == [1]
    ev.content = "hello world"
    got = matching_indexes(events, "world", key=key, stamp=stamp, turns={})
    spec = [int(item.index) for item in events if event_matches_query(item, "world", turn=None)]
    assert got == spec
    assert got == [1]
    rows, rebuilds, appends = index_stats(key)
    assert rows == 1
    assert rebuilds == 1
    assert appends == 0


def test_matching_indexes_refills_tail_on_append() -> None:
    ev = TraceEvent(index=1, event_type="user_message_chunk", content="hello")
    key = "mutate-tail"
    first_stamp: TimelineStamp = (1.0, 1, 0, 0)
    assert matching_indexes([ev], "hello", key=key, stamp=first_stamp, turns={}) == [1]
    ev.content = "hello world"
    nxt = TraceEvent(index=2, event_type="user_message_chunk", content="tail")
    grown = [ev, nxt]
    later_stamp: TimelineStamp = (2.0, 2, 0, 0)
    got = matching_indexes(grown, "world", key=key, stamp=later_stamp, turns={})
    spec = [int(item.index) for item in grown if event_matches_query(item, "world", turn=None)]
    assert got == spec
    assert 1 in got
    _rows, rebuilds, appends = index_stats(key)
    assert rebuilds == 1
    assert appends == 1


def _big_timeline(n: int = 10_000) -> tuple[list[TraceEvent], dict[int, int]]:
    body = "padding-" + ("z" * 4000)
    events: list[TraceEvent] = []
    turns: dict[int, int] = {}
    for i in range(n):
        turns[i] = i // 100
        if i % 50 == 0:
            events.append(
                TraceEvent(
                    index=i,
                    event_type="agent_message_chunk",
                    content=f"assistant hit {i}",
                )
            )
        elif i % 77 == 0:
            events.append(
                TraceEvent(
                    index=i,
                    event_type="tool_call",
                    tool_name="read_file",
                    content=body,
                    is_error=True,
                )
            )
        else:
            events.append(
                TraceEvent(
                    index=i,
                    event_type="tool_call",
                    tool_name="grep",
                    content=body,
                )
            )
    return events, turns


def test_ten_thousand_apply_uses_warm_index() -> None:
    """After events are indexed, every apply is a scan — not a full-text rebuild."""
    events, turns = _big_timeline()
    key = "big-10k"
    stamp: TimelineStamp = (5.0, len(events), 0, 0)
    ensure_indexed(events, key=key, stamp=stamp, turns=turns)
    rows, rebuilds, appends = index_stats(key)
    assert rows == len(events)
    assert rebuilds == 1
    assert appends == 0
    queries = (
        "is:assistant",
        "has:error",
        "tool:read",
        "turn:2",
        "assistant hit",
        "is:tool AND read_file",
    )
    for query in queries:
        spec = [
            int(ev.index)
            for ev in events
            if event_matches_query(ev, query, turn=turns.get(int(ev.index)))
        ]
        got = matching_indexes(events, query, key=key, stamp=stamp, turns=turns)
        assert got == spec, query
    rows2, rebuilds2, appends2 = index_stats(key)
    assert rows2 == len(events)
    assert rebuilds2 == 1
    assert appends2 == 0


def test_append_does_not_rebuild_old_rows() -> None:
    events, turns = _big_timeline()
    key = "append-10k"
    stamp: TimelineStamp = (6.0, len(events), 0, 0)
    ensure_indexed(events, key=key, stamp=stamp, turns=turns)
    rows, rebuilds, appends = index_stats(key)
    assert rows == len(events)
    assert rebuilds == 1
    assert appends == 0
    extra = TraceEvent(
        index=len(events),
        event_type="agent_message_chunk",
        content="brand-new-assistant",
    )
    grown = [*events, extra]
    turns[extra.index] = 99
    new_stamp: TimelineStamp = (7.0, len(grown), 0, 0)
    hits = matching_indexes(
        grown,
        "brand-new-assistant",
        key=key,
        stamp=new_stamp,
        turns=turns,
    )
    assert extra.index in hits
    old = matching_indexes(
        grown,
        "is:assistant",
        key=key,
        stamp=new_stamp,
        turns=turns,
    )
    assert extra.index in old
    assert len(old) >= 200
    rows2, rebuilds2, appends2 = index_stats(key)
    assert rows2 == len(grown)
    assert rebuilds2 == 1
    assert appends2 == 1


def test_build_session_timeline_query_uses_store(tmp_path: Path) -> None:
    sd = tmp_path / "tl-store"
    sd.mkdir()
    import json

    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "tl-store"}, "generated_title": "S"}),
        encoding="utf-8",
    )
    lines = [
        json.dumps(
            {
                "timestamp": 1000,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "hello user"},
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 1001,
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "ok"},
                    }
                },
            }
        ),
    ]
    (sd / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
    page = build_session_timeline(sd, offset=0, limit=50, query="is:assistant")
    assert page["total"] >= 1
    assert all(
        "agent" in str(ev.get("type") or "") or "assistant" in str(ev.get("type") or "")
        for ev in page["events"]
    )
