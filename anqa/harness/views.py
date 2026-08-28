"""Control-plane views for sessions whose locator is not a directory."""

from __future__ import annotations

from ..models import JsonObject, JsonValue, SessionMeta, TraceEvent
from ..notes import notes_snapshot
from ..session.catalog import catalog_row_sort_epoch
from ..session.control_views import (
    DEFAULT_CONTENT_CHARS,
    DEFAULT_TIMELINE_LIMIT,
    MAX_CONTENT_CHARS,
    MAX_TIMELINE_LIMIT,
    SessionOverview,
    session_meta_mapping,
    timeline_event_mapping,
    turn_segment_mapping,
)
from ..session.event_search import matching_indexes
from ..session.query import catalog_presence, turn_matches_query
from ..session.subagents import subagent_run_mapping, subagent_runs_for_session
from ..session.turns import (
    event_display_turn_map,
    event_matches_timeline_kind,
    segment_timeline_turns,
)
from .ref import SessionRef
from .registry import adapter, adapter_for


def _notes_dir(ref: SessionRef):
    path = ref.overlay_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _empty_presence() -> dict[str, bool | int]:
    return {
        "hasWorkflows": False,
        "hasNotes": False,
        "hasGoals": False,
        "hasSubagents": False,
        "hasJobs": False,
        "hasSchedules": False,
        "hasTasks": False,
        "hasPlan": False,
        "hasFailures": False,
        "hasDiff": False,
        "hasCompaction": False,
        "hasDoom": False,
        "hasContext": False,
        "workflowCount": 0,
        "noteCount": 0,
        "goalCount": 0,
        "planCount": 0,
        "subagentCount": 0,
        "taskCount": 0,
        "jobCount": 0,
        "scheduleCount": 0,
        "errorCount": 0,
        "failureCount": 0,
        "diffLineCount": 0,
        "compactionCount": 0,
        "doomCount": 0,
    }


def catalog_row_from_ref(ref: SessionRef) -> JsonObject | None:
    """Wire catalog row for an adapter session ref."""
    impl = adapter(ref.harness)
    if impl is None:
        return None
    try:
        meta = impl.load_meta(ref)
    except (OSError, FileNotFoundError):
        return None
    path_str = ref.ref_string()
    created = str(meta.created_at or "").strip()
    updated = str(meta.updated_at or "").strip()
    sort_epoch = catalog_row_sort_epoch({"updatedAt": updated, "createdAt": created})
    if sort_epoch <= 0:
        try:
            sort_epoch = float(ref.locator.stat().st_mtime)
        except OSError:
            sort_epoch = 0.0
    presence = _empty_presence()
    try:
        notes = catalog_presence(_notes_dir(ref), meta)
        presence["hasNotes"] = bool(notes.get("hasNotes"))
        presence["noteCount"] = int(notes.get("noteCount") or 0)
    except OSError:
        pass
    presence["errorCount"] = int(meta.error_count or 0)
    if meta.has_subagents:
        presence["hasSubagents"] = True
        presence["subagentCount"] = int(meta.subagent_count or 0)
    return {
        "sessionId": ref.session_id,
        "path": path_str,
        "harness": ref.harness,
        "harnessVersion": (meta.harness_version or "").strip(),
        "title": (meta.title or "").strip(),
        "label": meta.label,
        "model": meta.model_display,
        "status": meta.list_status_label(),
        "outcome": meta.turn_outcome or "",
        "origin": meta.origin or ref.origin,
        "taskId": meta.task_id or "",
        "gitRepo": meta.git_repo or "",
        "runDir": meta.run_dir or ref.cwd or "",
        "durationSeconds": float(meta.duration_seconds or 0),
        "numEvents": int(meta.num_events or 0),
        "contextUsageCompact": meta.context_usage_compact or "",
        "contextWindowUsagePct": meta.context_window_usage_pct,
        "contextTokensUsed": meta.context_tokens_used,
        "contextWindowTokens": meta.context_window_tokens,
        "toolCallCount": int(meta.tool_call_count or 0),
        "turnCount": int(meta.turn_count or 0),
        "errorCount": int(meta.error_count or 0),
        "createdAt": created,
        "updatedAt": updated,
        "sortEpoch": sort_epoch,
        **presence,
    }


def _load(ref: SessionRef) -> tuple[SessionMeta, list[TraceEvent]]:
    impl = adapter_for(ref)
    if impl is None:
        raise FileNotFoundError(f"unknown harness: {ref.harness}")
    return impl.load_meta(ref), impl.parse_timeline(ref)


def _subagent_rows(ref: SessionRef, events: list[TraceEvent]) -> list[JsonValue]:
    segs = segment_timeline_turns(events)
    turn_map = event_display_turn_map(segs)
    runs = subagent_runs_for_session(ref.locator, events, segs, turn_map)
    out: list[JsonValue] = []
    for run in runs:
        row = subagent_run_mapping(run)
        child = run.child_session_id
        if child and not str(row.get("childPath") or "").strip():
            row["childPath"] = f"{ref.harness}:{child}"
            row["openable"] = True
        out.append(row)
    return out


def session_overview(ref: SessionRef) -> JsonObject:
    """``session/overview`` without Grok jobs / rewind / subagent dirs."""
    meta, events = _load(ref)
    meta.num_events = len(events)
    segs = segment_timeline_turns(events)
    sub_rows = _subagent_rows(ref, events)
    notes_rev = ""
    notes_count = 0
    notes_rows: list[JsonValue] = []
    try:
        snap = notes_snapshot(_notes_dir(ref))
        notes_rev = snap.revision
        notes_count = len(snap.doc.notes)
        for note in snap.doc.sorted_notes()[:40]:
            notes_rows.append(
                {
                    "id": note.id,
                    "turnIndex": note.turn_index,
                    "source": note.source,
                    "fields": dict(note.fields),
                    "eventIndices": list(note.event_indices),
                    "createdAt": note.created_at,
                    "updatedAt": note.updated_at,
                }
            )
    except OSError:
        pass
    mapped = session_meta_mapping(meta, path=None, origin=meta.origin)
    mapped["path"] = ref.ref_string()
    mapped["harness"] = ref.harness
    return {
        "sessionId": ref.session_id,
        "meta": mapped,
        "summary": (meta.summary_text or "").strip(),
        "backgroundJobs": [],
        "schedules": [],
        "workflows": [],
        "turns": {
            "total": len(segs),
            "turns": [
                turn_segment_mapping(
                    s,
                    include_event_indexes=False,
                    assistant_max_chars=400,
                    subagent_runs=[],
                )
                for s in segs
            ],
            "subagentRuns": sub_rows,
        },
        "timeline": {
            "total": len(events),
            "offset": 0,
            "limit": 0,
            "truncated": False,
            "events": [],
            "lazy": True,
        },
        "notes": {
            "revision": notes_rev,
            "count": notes_count,
            "notes": notes_rows,
            "schema": SessionOverview.notes_schema(),
        },
        "stats": {"eventTypes": [], "tools": []},
    }


def session_timeline(
    ref: SessionRef,
    *,
    offset: int = 0,
    limit: int | None = None,
    event_type: str = "",
    kind: str = "",
    query: str = "",
    prompt_index: int | None = None,
    around_index: int | None = None,
    at_index: int | None = None,
    content_chars: int | None = None,
) -> JsonObject:
    """Paged ``session/timeline`` from the adapter parse."""
    _meta, events = _load(ref)
    segs = segment_timeline_turns(events)
    turn_by_index = event_display_turn_map(segs)
    type_filter = (event_type or "").strip().casefold()
    query_hits: set[int] | None = None
    if query.strip():
        query_hits = set(
            matching_indexes(
                events,
                query,
                key=ref.ref_string(),
                stamp=(0.0, 0, 0, 0),
                turns=turn_by_index,
            )
        )
    filtered: list[TraceEvent] = []
    for ev in events:
        if type_filter and type_filter not in (ev.event_type or "").casefold():
            if type_filter not in (ev.type_label or "").casefold():
                continue
        if not event_matches_timeline_kind(ev, kind):
            continue
        if query_hits is not None and int(ev.index) not in query_hits:
            continue
        filtered.append(ev)
    total = len(filtered)
    off = max(0, int(offset))
    lim = DEFAULT_TIMELINE_LIMIT if limit is None else max(0, min(int(limit), MAX_TIMELINE_LIMIT))
    if at_index is not None:
        target = int(at_index)
        hit = next((i for i, ev in enumerate(filtered) if int(ev.index) == target), None)
        off, lim = (0, 0) if hit is None else (hit, 1)
    elif around_index is not None:
        target = int(around_index)
        hit = next((i for i, ev in enumerate(filtered) if int(ev.index) >= target), None)
        if hit is None and filtered:
            hit = len(filtered) - 1
        if hit is not None:
            off = max(0, hit - 8)
    cc = (
        DEFAULT_CONTENT_CHARS
        if content_chars is None
        else max(0, min(int(content_chars), MAX_CONTENT_CHARS))
    )
    page = filtered[off : off + lim] if lim else []
    return {
        "sessionId": ref.session_id,
        "total": total,
        "offset": off,
        "limit": lim,
        "events": [
            timeline_event_mapping(
                ev,
                content_chars=cc,
                turn_index=turn_by_index.get(int(ev.index)),
            )
            for ev in page
        ],
    }


def session_turns(ref: SessionRef, *, query: str = "") -> JsonObject:
    """``session/turns`` from the adapted timeline."""
    _meta, events = _load(ref)
    segs = segment_timeline_turns(events)
    needle = (query or "").strip()
    if needle:
        segs = [
            seg
            for seg in segs
            if turn_matches_query(
                label=seg.label,
                summary=seg.user_prompt_preview()[0],
                outcome=seg.outcome or "",
                error_count=int(seg.error_event_count) + int(seg.tool_error_count),
                tool_count=int(seg.tool_call_count),
                event_count=int(seg.event_count),
                duration_seconds=int(seg.duration_seconds() or 0),
                subagent_count=0,
                query=needle,
            )
        ]
    return {
        "sessionId": ref.session_id,
        "total": len(segs),
        "turns": [turn_segment_mapping(s, subagent_runs=[]) for s in segs],
        "subagentRuns": _subagent_rows(ref, events),
    }


def session_diff(ref: SessionRef) -> JsonObject:
    """No rewind store — empty diff."""
    return {"sessionId": ref.session_id, "source": "", "points": []}


__all__ = [
    "catalog_row_from_ref",
    "session_diff",
    "session_overview",
    "session_timeline",
    "session_turns",
]
