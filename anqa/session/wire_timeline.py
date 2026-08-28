"""Rebuild domain timeline types from control wire payloads (TUI attach path).

Clients that must not parse session disk (TUI when attached, HUD) consume
``session/timeline`` / ``session/overview`` JSON and hydrate
:class:`~anqa.models.TraceEvent` / :class:`~anqa.models.SessionMeta` here.
"""

from __future__ import annotations

from pathlib import Path

from ..models import JsonObject, JsonValue, SessionMeta, ToolInputBag, TraceEvent, as_json_object
from .catalog import session_meta_from_catalog_row
from .control_views import MAX_CONTENT_CHARS, MAX_TIMELINE_LIMIT

# One RPC must finish inside HEAVY_RPC_TIMEOUT. HUD uses 200 × 12k; the TUI
# used MAX_TIMELINE_LIMIT × MAX_CONTENT_CHARS and hit the 5s client timeout.
TIMELINE_RPC_LIMIT = 200
TIMELINE_RPC_CHARS = 12_000


def trace_event_from_wire(row: JsonObject) -> TraceEvent:
    """Hydrate one :class:`TraceEvent` from a ``session/timeline`` event object."""
    raw_in = row.get("rawInput")
    raw: dict[str, JsonValue] = {}
    if isinstance(raw_in, dict):
        raw = {str(k): v for k, v in raw_in.items()}

    ts = row.get("timestamp")
    timestamp: int | None
    if isinstance(ts, bool):
        timestamp = None
    elif isinstance(ts, int):
        timestamp = ts
    elif isinstance(ts, float):
        timestamp = int(ts)
    elif isinstance(ts, str) and ts.strip().isdigit():
        timestamp = int(ts.strip())
    else:
        timestamp = None

    pi = row.get("promptIndex")
    prompt_index: int | None
    if isinstance(pi, bool) or pi is None or pi == "":
        prompt_index = None
    elif isinstance(pi, int):
        prompt_index = pi
    elif isinstance(pi, float):
        prompt_index = int(pi)
    elif isinstance(pi, str):
        try:
            prompt_index = int(pi)
        except ValueError:
            prompt_index = None
    else:
        prompt_index = None

    idx = row.get("index")
    index = int(idx) if isinstance(idx, (int, float)) and not isinstance(idx, bool) else 0
    upd = row.get("updateIndex")
    update_index = int(upd) if isinstance(upd, (int, float)) and not isinstance(upd, bool) else 0

    event_type = str(row.get("type") or "").strip()
    images: list[bytes] = []
    image_path = str(row.get("imagePath") or "").strip()
    if event_type == "user_message_chunk" and image_path:
        dest = Path(image_path)
        if dest.is_file():
            images = [dest.read_bytes()]
    return TraceEvent(
        index=index,
        event_type=event_type,
        timestamp=timestamp,
        content=str(row.get("content") or ""),
        tool_name=str(row.get("toolName") or "").strip(),
        tool_call_id=str(row.get("toolCallId") or "").strip(),
        raw_input=ToolInputBag(raw),
        is_error=bool(row.get("isError")),
        update_index=update_index,
        prompt_index=prompt_index,
        images=images,
    )


def _timeline_page_bounds(page_limit: int, content_chars: int) -> tuple[int, int]:
    """Clamp a page request to the owner's accepted range."""
    lim = max(1, min(int(page_limit), MAX_TIMELINE_LIMIT))
    chars = max(0, min(int(content_chars), MAX_CONTENT_CHARS))
    return lim, chars


def _events_from_timeline_page(page: object, *, offset: int) -> tuple[list[TraceEvent], int]:
    """Parse one ``session/timeline`` result into events and the owner total."""
    if not isinstance(page, dict):
        return [], 0
    batch = page.get("events")
    events: list[TraceEvent] = []
    if isinstance(batch, list):
        for item in batch:
            if isinstance(item, dict):
                events.append(trace_event_from_wire(as_json_object(item)))
    total = page.get("total")
    try:
        total_n = int(total) if total is not None else (int(offset) + len(events))
    except (TypeError, ValueError):
        total_n = int(offset) + len(events)
    return events, total_n


async def fetch_timeline_page(
    access: object,
    session_ref: str,
    *,
    offset: int = 0,
    page_limit: int = TIMELINE_RPC_LIMIT,
    content_chars: int = TIMELINE_RPC_CHARS,
) -> tuple[list[TraceEvent], int]:
    """One ``session/timeline`` page and the owner's event total.

    :param access: Object with async ``session_timeline``.
    :param session_ref: Session id or path accepted by control.
    :param offset: First event index in the owner's list.
    :returns: ``(events, total)``. ``total`` is 0 when the response is unusable.
    :raises TypeError: When *access* has no ``session_timeline``.
    """
    session_timeline = getattr(access, "session_timeline", None)
    if not callable(session_timeline):
        raise TypeError("access must provide session_timeline")
    lim, chars = _timeline_page_bounds(page_limit, content_chars)
    pos = max(0, int(offset))
    page = await session_timeline(
        session_ref,
        offset=pos,
        limit=lim,
        content_chars=chars,
    )
    return _events_from_timeline_page(page, offset=pos)


async def fetch_timeline_event(
    access: object,
    session_ref: str,
    index: int,
    *,
    content_chars: int = MAX_CONTENT_CHARS,
) -> TraceEvent | None:
    """One event by index, at the owner's content ceiling.

    :param access: Object with async ``session_timeline``.
    :param session_ref: Session id or path accepted by control.
    :param index: Event index in the session timeline.
    :param content_chars: Body cap (clamped to ``MAX_CONTENT_CHARS``).
    :returns: The event, or ``None`` when the owner has no such row.
    """
    session_timeline = getattr(access, "session_timeline", None)
    if not callable(session_timeline):
        raise TypeError("access must provide session_timeline")
    _, chars = _timeline_page_bounds(1, content_chars)
    page = await session_timeline(
        session_ref,
        offset=0,
        limit=1,
        at_index=int(index),
        content_chars=chars,
    )
    events, _total = _events_from_timeline_page(page, offset=0)
    return events[0] if events else None


async def fetch_timeline_events(
    access: object,
    session_ref: str,
    *,
    content_chars: int = TIMELINE_RPC_CHARS,
    page_limit: int = TIMELINE_RPC_LIMIT,
    offset: int = 0,
) -> list[TraceEvent]:
    """Page ``session/timeline`` until complete; return domain events.

    :param access: Object with async ``session_timeline`` (RemoteSessionAccess).
    :param session_ref: Session id or path accepted by control.
    :param offset: Start at this owner index (live tail after events already held).
    """
    out: list[TraceEvent] = []
    pos = max(0, int(offset))
    lim, chars = _timeline_page_bounds(page_limit, content_chars)
    while True:
        batch, total_n = await fetch_timeline_page(
            access,
            session_ref,
            offset=pos,
            page_limit=lim,
            content_chars=chars,
        )
        if not batch:
            break
        out.extend(batch)
        pos += len(batch)
        if pos >= total_n or len(batch) < lim:
            break
    return out


async def fetch_timeline_growth(
    access: object,
    session_ref: str,
    *,
    held: list[TraceEvent],
    new_total: int,
) -> list[TraceEvent]:
    """Append events after *held*, or refill when the owner list shrank.

    :param held: Events already in the browser.
    :param new_total: Owner ``num_events`` from the latest overview.
    :returns: Full list to store (held + tail, or a complete refill).
    """
    prev_n = len(held)
    want = max(0, int(new_total))
    if held and want > prev_n:
        last = await fetch_timeline_event(access, session_ref, held[-1].index)
        tail = await fetch_timeline_events(access, session_ref, offset=prev_n)
        out = list(held)
        if last is not None:
            out[-1] = last
        if tail:
            out.extend(tail)
        return out
    if held and want == prev_n:
        last = await fetch_timeline_event(access, session_ref, held[-1].index)
        if last is None:
            return list(held)
        out = list(held)
        out[-1] = last
        return out
    return await fetch_timeline_events(access, session_ref)


def session_meta_from_overview(overview: JsonObject, *, fallback_dir: Path) -> SessionMeta:
    """Build :class:`SessionMeta` from a ``session/overview`` payload."""
    meta_raw = overview.get("meta")
    row = as_json_object(meta_raw) if isinstance(meta_raw, dict) else {}
    if not row.get("path") and not row.get("sessionId"):
        row = {
            **row,
            "path": str(fallback_dir),
            "sessionId": fallback_dir.name,
        }
    meta = session_meta_from_catalog_row(row)
    if meta is None:
        meta = SessionMeta(session_id=fallback_dir.name, session_dir=fallback_dir)
    # Prefer resolved path from wire when present.
    path_raw = str(row.get("path") or "").strip()
    if path_raw:
        try:
            meta.session_dir = Path(path_raw).expanduser()
        except OSError:
            pass
    summary = overview.get("summary")
    if isinstance(summary, str) and summary.strip():
        meta.summary_text = summary.strip()
    tl = overview.get("timeline")
    if isinstance(tl, dict) and tl.get("total") is not None:
        try:
            meta.num_events = int(tl["total"])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass
    return meta


async def fetch_session_browser_bundle(
    access: object,
    session_ref: str,
    *,
    fallback_dir: Path,
) -> tuple[SessionMeta, list[TraceEvent], JsonObject]:
    """Load meta + full timeline for the session browser via control.

    :returns: ``(meta, timeline, overview_payload)``.
    """
    session_overview = getattr(access, "session_overview", None)
    if not callable(session_overview):
        raise TypeError("access must provide session_overview")
    overview = await session_overview(session_ref)
    if not isinstance(overview, dict):
        overview = {}
    ov = as_json_object(overview)
    meta = session_meta_from_overview(ov, fallback_dir=fallback_dir)
    events = await fetch_timeline_events(access, session_ref)
    meta.num_events = len(events)
    return meta, events, ov


__all__ = [
    "TIMELINE_RPC_CHARS",
    "TIMELINE_RPC_LIMIT",
    "fetch_session_browser_bundle",
    "fetch_timeline_event",
    "fetch_timeline_events",
    "fetch_timeline_growth",
    "fetch_timeline_page",
    "session_meta_from_overview",
    "trace_event_from_wire",
]
