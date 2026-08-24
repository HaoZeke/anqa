"""In-process Timeline search index on :class:`TraceEvent`.

Structured tokens (``is:``, ``has:``, ``tool:``, ``turn:``) read columns.
Bare words and ``user:`` read a casefolded hay cache filled at index time
or on first text query, appended when the timeline grows.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .. import event_types as et
from ..models import TraceEvent
from ..parser import TimelineStamp
from .query import (
    ListQueryBag,
    compile_bag_predicate,
    finished_prefix,
    query_needs_hay,
)
from .subagents import event_child_session_id, subagent_duration_seconds
from .turns import event_matches_timeline_kind

_KIND_MODES: tuple[tuple[str, str], ...] = (
    ("tool", "tools"),
    ("user", "user"),
    ("assistant", "assistant"),
    ("error", "error"),
    ("session", "session"),
    ("subagent", "subagent"),
    ("background", "background"),
    ("workflow", "workflow"),
)

_lock = threading.Lock()
_indexes: dict[str, _SessionIndex] = {}


@dataclass
class EventRow:
    """One indexed timeline event."""

    index: int
    kinds: frozenset[str]
    is_error: bool
    tool: str
    turn: int | None
    user_hay: str
    hay: str
    hay_ready: bool
    duration: int | None = None
    hay_sig: tuple[int, str, str, str] = (0, "", "", "")


@dataclass
class _SessionIndex:
    stamp: TimelineStamp
    rows: list[EventRow]
    rebuilds: int = 0
    appends: int = 0


def event_durations(events: Sequence[TraceEvent]) -> dict[int, float]:
    """Pair seconds shown in Timeline Dur.

    Tool calls use the matching result timestamp. Other events use the gap
    to the next timestamp. Subagent finish length overrides both.
    """
    out: dict[int, float] = {}
    if not events:
        return out
    result_ts: dict[str, int | float] = {}
    for ev in events:
        if ev.event_type in et.TOOL_UPDATE_TYPES and ev.tool_call_id and ev.timestamp:
            result_ts[ev.tool_call_id] = ev.timestamp
    mates = _subagent_spawn_mates(events)
    for i, ev in enumerate(events):
        if ev.timestamp is None:
            continue
        if ev.event_type == "tool_call" and ev.tool_call_id in result_ts:
            dur = result_ts[ev.tool_call_id] - ev.timestamp
            if dur >= 0:
                out[int(ev.index)] = float(dur)
        elif ev.event_type in et.TOOL_UPDATE_TYPES:
            pass
        else:
            ev_ts = ev.timestamp
            for nxt in events[i + 1 :]:
                next_ts = nxt.timestamp
                if next_ts is not None:
                    dur = next_ts - ev_ts
                    if dur >= 0:
                        out[int(ev.index)] = float(dur)
                    break
        own = subagent_duration_seconds(ev)
        if own is not None:
            out[int(ev.index)] = own
    for ev in events:
        if ev.event_type != "subagent_spawned":
            continue
        mate = mates.get(int(ev.index))
        if mate is None:
            continue
        own = subagent_duration_seconds(mate)
        if own is not None:
            out[int(ev.index)] = own
    return out


def matching_indexes(
    events: Sequence[TraceEvent],
    query: str,
    *,
    key: str,
    stamp: TimelineStamp,
    turns: Mapping[int, int] | None = None,
) -> list[int]:
    """Event indexes that satisfy *query*, in timeline order.

    :param events: Normalized timeline for *key*.
    :param query: Catalog query language (same tree as ``event_matches_query``).
    :param key: Stable session identity (resolved directory, or a table id).
    :param stamp: Timeline stamp; growth appends, identity change rebuilds.
    :param turns: ``event.index`` → enclosing turn index.
    :returns: Matching ``TraceEvent.index`` values.
    """
    text = finished_prefix(query).strip()
    if not text:
        return [int(ev.index) for ev in events]
    turn_map = turns or {}
    need_hay = query_needs_hay(query)
    pred = compile_bag_predicate(query)
    with _lock:
        idx = _ensure(events, key, stamp, turn_map, hay=need_hay)
        rows = idx.rows
        return [row.index for row in rows if pred(_bag(row))]


def ensure_indexed(
    events: Sequence[TraceEvent],
    *,
    key: str,
    stamp: TimelineStamp,
    turns: Mapping[int, int] | None = None,
) -> None:
    """Index *events* (columns and hay) so later searches only scan."""
    with _lock:
        _ensure(events, key, stamp, turns or {}, hay=True)


def index_covers(
    key: str,
    stamp: TimelineStamp,
    count: int,
    *,
    hay: bool,
) -> bool:
    """True when *key* already has *count* rows at *stamp* (and hay if asked)."""
    with _lock:
        idx = _indexes.get(key)
        if idx is None or idx.stamp != stamp or len(idx.rows) != count:
            return False
        if hay and any(not row.hay_ready for row in idx.rows):
            return False
        return True


def scan_index(key: str, query: str) -> list[int]:
    """Scan a warm index. Empty when *key* is missing."""
    text = finished_prefix(query).strip()
    if not text:
        with _lock:
            idx = _indexes.get(key)
            return [row.index for row in idx.rows] if idx is not None else []
    pred = compile_bag_predicate(query)
    with _lock:
        idx = _indexes.get(key)
        if idx is None:
            return []
        return [row.index for row in idx.rows if pred(_bag(row))]


def index_stats(key: str) -> tuple[int, int, int]:
    """``(row_count, rebuilds, appends)`` for *key*, or zeros."""
    with _lock:
        idx = _indexes.get(key)
    if idx is None:
        return (0, 0, 0)
    return (len(idx.rows), idx.rebuilds, idx.appends)


def reset_indexes() -> None:
    """Drop every in-process index (tests)."""
    with _lock:
        _indexes.clear()


def _ensure(
    events: Sequence[TraceEvent],
    key: str,
    stamp: TimelineStamp,
    turns: Mapping[int, int],
    *,
    hay: bool,
) -> _SessionIndex:
    idx = _indexes.get(key)
    if idx is not None and idx.stamp == stamp and len(idx.rows) == len(events):
        if hay:
            _fill_hay(idx, events)
        _apply_durations(idx, events)
        return idx
    if idx is not None and _prefix_matches(idx, events):
        start = len(idx.rows)
        if start:
            _refresh_row(idx.rows[start - 1], events[start - 1], turns)
        idx.rows.extend(_column_row(ev, turns) for ev in events[start:])
        idx.stamp = stamp
        idx.appends += 1
        if hay:
            _fill_hay(idx, events)
        _apply_durations(idx, events)
        return idx
    rows = [_column_row(ev, turns) for ev in events]
    rebuilds = 1 if idx is None else idx.rebuilds + 1
    idx = _SessionIndex(stamp=stamp, rows=rows, rebuilds=rebuilds, appends=0)
    _indexes[key] = idx
    if hay:
        _fill_hay(idx, events)
    _apply_durations(idx, events)
    return idx


def _prefix_matches(idx: _SessionIndex, events: Sequence[TraceEvent]) -> bool:
    if len(events) < len(idx.rows):
        return False
    return all(int(events[i].index) == row.index for i, row in enumerate(idx.rows))


def _column_row(event: TraceEvent, turns: Mapping[int, int]) -> EventRow:
    kinds = frozenset(
        name for name, mode in _KIND_MODES if event_matches_timeline_kind(event, mode)
    )
    turn = turns.get(int(event.index))
    return EventRow(
        index=int(event.index),
        kinds=kinds,
        is_error=bool(event.is_error),
        tool=(event.tool_name or "").casefold(),
        turn=int(turn) if turn is not None else None,
        user_hay="",
        hay="",
        hay_ready=False,
    )


def _hay_sig(event: TraceEvent) -> tuple[int, str, str, str]:
    body = event.content if isinstance(event.content, str) else str(event.content or "")
    tool = event.tool_name or ""
    if len(body) <= 128:
        return (len(body), body, "", tool)
    return (len(body), body[:64], body[-64:], tool)


def _write_hay(row: EventRow, event: TraceEvent) -> None:
    body = event.content if isinstance(event.content, str) else str(event.content or "")
    row.hay = " ".join(
        part
        for part in (
            event.event_type,
            event.type_label,
            event.tool_name,
            event.summary_line,
            body,
        )
        if part
    ).casefold()
    row.user_hay = body.casefold() if "user" in row.kinds else ""
    row.hay_ready = True
    row.hay_sig = _hay_sig(event)


def _refresh_row(row: EventRow, event: TraceEvent, turns: Mapping[int, int]) -> None:
    fresh = _column_row(event, turns)
    row.kinds = fresh.kinds
    row.is_error = fresh.is_error
    row.tool = fresh.tool
    row.turn = fresh.turn
    row.duration = fresh.duration
    row.hay_ready = False
    row.hay_sig = (0, "", "", "")


def _apply_durations(idx: _SessionIndex, events: Sequence[TraceEvent]) -> None:
    durs = event_durations(events)
    for row in idx.rows:
        taken = durs.get(row.index)
        row.duration = int(taken) if taken is not None else None


def _fill_hay(idx: _SessionIndex, events: Sequence[TraceEvent]) -> None:
    n = min(len(idx.rows), len(events))
    for i in range(n):
        row = idx.rows[i]
        ev = events[i]
        sig = _hay_sig(ev)
        if row.hay_ready and row.hay_sig == sig:
            continue
        _write_hay(row, ev)


def _bag(row: EventRow) -> ListQueryBag:
    counts: dict[str, int] = {"errors": int(row.is_error)}
    if row.duration is not None:
        counts["duration"] = row.duration
    return ListQueryBag(
        hay=row.hay,
        has={"error": row.is_error},
        counts=counts,
        kinds=row.kinds,
        tool=row.tool,
        turn=row.turn,
        user_hay=row.user_hay,
    )


def _subagent_spawn_mates(events: Sequence[TraceEvent]) -> dict[int, TraceEvent]:
    by_child: dict[str, list[TraceEvent]] = {}
    for ev in events:
        if ev.event_type not in et.SUBAGENT_TYPES:
            continue
        child = event_child_session_id(ev)
        if child:
            by_child.setdefault(child, []).append(ev)
    mates: dict[int, TraceEvent] = {}
    for group in by_child.values():
        spawn = next((e for e in group if e.event_type == "subagent_spawned"), None)
        finish = next((e for e in group if e.event_type == "subagent_finished"), None)
        if spawn is not None and finish is not None:
            mates[int(spawn.index)] = finish
    return mates


__all__ = [
    "EventRow",
    "ensure_indexed",
    "event_durations",
    "index_covers",
    "index_stats",
    "matching_indexes",
    "reset_indexes",
    "scan_index",
]
