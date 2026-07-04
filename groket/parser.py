"""Trace parser — reads session directories into structured data."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .constants import INCOMPLETE_STALE_SECONDS, INTERRUPTED_MARKER_FILENAME
from .models import (
    ChatMessage,
    JsonObject,
    JsonValue,
    SessionMeta,
    ToolCall,
    ToolInput,
    ToolInputBag,
    TraceEvent,
    as_json_object,
    json_as_str,
)
from .paths import RUN_PREFIXES, is_run_dir_name, strip_run_prefix

logger = logging.getLogger(__name__)

try:
    import orjson as _orjson

    def json_loads(data: str | bytes) -> JsonValue:
        """Parse one JSON document (orjson on the timeline hot path)."""
        if isinstance(data, str):
            raw = _orjson.loads(data.encode("utf-8"))
        else:
            raw = _orjson.loads(data)
        return cast(JsonValue, raw)

except ImportError:  # pragma: no cover — orjson is a hard dependency

    def json_loads(data: str | bytes) -> JsonValue:
        if isinstance(data, bytes):
            return cast(JsonValue, json.loads(data.decode("utf-8")))
        return cast(JsonValue, json.loads(data))


def json_object_line(line: str | bytes) -> JsonObject | None:
    """Parse a JSONL line that must be an object; None if invalid or not a map."""
    try:
        val = json_loads(line)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(val, dict):
        return None
    return as_json_object(val)


# session_dir resolve key -> (trace mtime, timeline)
_timeline_cache: dict[str, tuple[float, list[TraceEvent]]] = {}

# Wrapper tool ids whose real target lives in ``rawInput.tool_name`` (MCP bridge).
# Compare with :func:`_tool_id_key` so ``use-tool`` / ``UseTool`` match ``use_tool``.
_MCP_WRAPPER_TOOL_IDS = frozenset(
    {
        "use_tool",
        "call_mcp",
        "call_mcp_tool",
        "mcp_tool",
    }
)

# Finite map from observed Grok *human titles* → stable tool ids (case-insensitive;
# trailing ``:`` stripped before lookup). Do not guess unknown titles.
_HUMAN_TITLE_TO_TOOL_ID: dict[str, str] = {
    "web search": "web_search",
}


def _tool_id_key(name: str) -> str:
    """Case-fold and unify ``-`` / ``_`` for wrapper-id membership checks."""
    return (name or "").strip().lower().replace("-", "_")


def normalize_tool_id(name: str) -> str:
    """Stable tool id for timeline storage and usage attribution.

    - Known human titles (e.g. ``Web search:``) map via :data:`_HUMAN_TITLE_TO_TOOL_ID`.
    - Otherwise the string is kept as-is (host ``grep``, MCP ``server__method``).
    """
    s = (name or "").strip()
    if not s:
        return "unknown"
    key = s.lower().rstrip(":").strip()
    return _HUMAN_TITLE_TO_TOOL_ID.get(key, s)


def resolve_tool_display_name(title: str, raw_input: ToolInput | None = None) -> str:
    """Resolve the tool id stored on a timeline event.

    Contract:

    1. If ``title`` is an MCP wrapper (``use_tool`` / ``call_mcp`` / …) and
       ``rawInput`` has ``tool_name`` or ``name``, use that nested id.
    2. Otherwise use ``title``.
    3. Run the result through :func:`normalize_tool_id` (human-title map only).

    :param title: Grok update / tool_call title field.
    :param raw_input: Structured tool arguments when present (dict or bag).
    :returns: Canonical tool id for :attr:`~groket.models.TraceEvent.tool_name`.
    """
    title_s = (title or "").strip() or "unknown"
    bag = ToolInputBag.ensure(raw_input)
    nested_s = (bag.as_str("tool_name") or bag.as_str("name")).strip()

    if nested_s and _tool_id_key(title_s) in {_tool_id_key(w) for w in _MCP_WRAPPER_TOOL_IDS}:
        return normalize_tool_id(nested_s)
    return normalize_tool_id(title_s)


def _as_epoch_ts(value: str | int | float | bool | None) -> int | None:
    """Coerce event timestamps to epoch seconds."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _parse_runtime_ts(ev: dict) -> int | None:
    """Epoch seconds from an events.jsonl row (ISO string or numeric)."""
    ts_raw = ev.get("ts") or ev.get("timestamp")
    if ts_raw is None:
        return None
    if isinstance(ts_raw, (int, float)):
        v = int(ts_raw)
        if v > 10_000_000_000:
            v = v // 1000
        return v
    try:
        dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ValueError, TypeError, OverflowError):
        return None


def parse_runtime_markers(session_dir: Path) -> tuple[list[TraceEvent], str, int]:
    """Parse events.jsonl for turn/session markers.

    Returns (marker_events_without_index, turn_outcome, loop_count).
    Marker events are not yet indexed; caller assigns indices.
    """
    events_file = session_dir / "events.jsonl"
    if not events_file.exists():
        return [], "", 0

    markers: list[TraceEvent] = []
    turn_outcome = ""
    loop_count = 0
    started: list[TraceEvent] = []
    ended: list[TraceEvent] = []

    try:
        with open(events_file) as f:
            for line_no, line in enumerate(f):
                ev = json_object_line(line)

                if ev is None:
                    continue
                et = ev.get("type") or ""
                ts = _parse_runtime_ts(ev)

                if et == "turn_started":
                    mid = ev.get("model_id") or ""
                    tn = ev.get("turn_number")
                    parts = ["turn started"]
                    if tn is not None:
                        parts.append(f"turn_number={tn}")
                    if mid:
                        parts.append(f"model={mid}")
                    started.append(
                        TraceEvent(
                            index=0,
                            event_type="turn_started",
                            timestamp=ts,
                            content="  ".join(parts),
                            update_index=line_no,
                        )
                    )

                elif et == "turn_ended":
                    outcome = str(ev.get("outcome") or ev.get("status") or "unknown")
                    turn_outcome = outcome
                    is_err = outcome.lower() not in (
                        "",
                        "success",
                        "ok",
                        "completed",
                        "complete",
                    )
                    extra = []
                    for k in ("error", "message", "reason", "detail"):
                        if ev.get(k):
                            extra.append(f"{k}={ev[k]}")
                    body = f"turn ended  outcome={outcome}"
                    if extra:
                        body += "  " + "  ".join(str(x) for x in extra)
                    ended.append(
                        TraceEvent(
                            index=0,
                            event_type="turn_ended",
                            timestamp=ts,
                            content=body,
                            is_error=is_err,
                            update_index=line_no,
                        )
                    )

                elif et == "loop_started":
                    try:
                        li = ev.get("loop_index", 0)
                        loop_count = max(
                            loop_count,
                            int(li) + 1 if isinstance(li, (int, float, str)) else 0,
                        )
                    except (TypeError, ValueError):
                        pass

                elif et in ("error", "session_error", "turn_error", "fatal_error"):
                    msg = ev.get("message") or ev.get("error") or ev.get("detail") or str(ev)[:200]
                    if not turn_outcome:
                        turn_outcome = "error"
                    ended.append(
                        TraceEvent(
                            index=0,
                            event_type="session_error",
                            timestamp=ts,
                            content=f"{et}: {msg}"[:500],
                            is_error=True,
                            update_index=line_no,
                        )
                    )
    except OSError:
        return [], "", 0

    markers = started + ended
    return markers, turn_outcome, loop_count


def _stringify_tool_payload(value: object) -> str:
    """Turn a tool output field into display text (str, MCP wrappers, JSON)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        # MCP / plugin wrappers: prefer success then error then nested content.
        for key in (
            "OkayOutput",
            "okay_output",
            "ErrorOutput",
            "error_output",
            "output",
            "content",
            "text",
            "FileContent",
            "Content",
            "stdout",
        ):
            if key in value:
                inner = _stringify_tool_payload(value.get(key))
                if inner:
                    return inner
        try:
            return json.dumps(value, indent=2)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, list):
        parts = [_stringify_tool_payload(item) for item in value]
        return "\n".join(p for p in parts if p)
    return str(value)


def _extract_raw_output_text(raw_output: object) -> str:
    """Body text from ``tool_call_update.rawOutput`` (host tools + MCP).

    Host tools often set ``output_for_prompt`` / ``output`` / ``FileContent``.
    MCP tools set ``type=MCP`` with ``output`` as ``{OkayOutput: ...}`` (or
    ``ErrorOutput``) and leave ``content`` empty — we must read that path or
    the timeline shows no result for context7 / playwright / etc.
    """
    if not isinstance(raw_output, dict):
        return _stringify_tool_payload(raw_output) if raw_output is not None else ""
    for key in (
        "output_for_prompt",
        "output",
        "content",
        "FileContent",
        "Content",
        "stdout",
        "stderr",
    ):
        if key not in raw_output:
            continue
        text = _stringify_tool_payload(raw_output.get(key))
        if text:
            return text
    return ""


def _apply_tool_result_meta(tc: ToolCall, update: dict) -> None:
    """Apply rawOutput metadata and error status from a tool_call_update."""
    raw_output = update.get("rawOutput")
    if isinstance(raw_output, dict):
        body = _extract_raw_output_text(raw_output)
        if body:
            ofp = (
                raw_output.get("output_for_prompt")
                if isinstance(raw_output.get("output_for_prompt"), str)
                else ""
            )
            if (
                ofp
                and ofp.startswith("exit:")
                and not (tc.result_content or "").startswith("exit:")
            ):
                tc.result_content = ofp
            elif not tc.result_content or len(body) >= len(tc.result_content):
                tc.result_content = body
        exit_code = raw_output.get("exit_code")
        signal = raw_output.get("signal")
        if exit_code is not None:
            tc.exit_code = exit_code
        if signal:
            tc.signal = signal

    is_error = update.get("isError")
    status = update.get("status", "")
    if is_error is True or status == "failed":
        tc.is_error = True

    # exit_code=1 is often benign (grep no-match, diff differences);
    # only treat exit_code >= 2 or signals as errors for terminal commands.
    if not tc.is_error and tc.tool_name == "run_terminal_command":
        if tc.signal:
            tc.is_error = True
        elif tc.exit_code is not None and tc.exit_code not in (0, 1):
            tc.is_error = True


def parse_tool_calls(session_dir: Path) -> list[ToolCall]:
    """Parse updates.jsonl to extract tool calls with their results."""
    updates_file = session_dir / "updates.jsonl"
    if not updates_file.exists():
        return []

    tool_calls: dict[str, ToolCall] = {}
    call_order: list[ToolCall] = []

    with open(updates_file) as f:
        for idx, line in enumerate(f):
            event = json_object_line(line)

            if event is None:
                continue

            params = event.get("params")
            update_raw = params.get("update") if isinstance(params, dict) else None
            update: JsonObject = as_json_object(update_raw) if isinstance(update_raw, dict) else {}
            event_type = str(update.get("sessionUpdate") or "")
            timestamp = _as_epoch_ts(
                event.get("timestamp")  # type: ignore[arg-type]  # JsonValue; narrowed below
                if isinstance(event.get("timestamp"), (str, int, float))
                or event.get("timestamp") is None
                else None
            )

            if event_type == "tool_call":
                call_id = json_as_str(update.get("toolCallId"))
                raw_input = update.get("rawInput", {})
                tool_name = resolve_tool_display_name(
                    json_as_str(update.get("title")) or "unknown",
                    ToolInputBag(raw_input) if isinstance(raw_input, dict) else None,
                )
                tc = ToolCall(
                    call_id=call_id,
                    tool_name=tool_name,
                    raw_input=ToolInputBag(raw_input)
                    if isinstance(raw_input, dict)
                    else ToolInputBag(),
                    timestamp=timestamp,
                    update_index=idx,
                )
                tool_calls[call_id] = tc
                call_order.append(tc)

            elif event_type == "tool_call_update":
                call_id = json_as_str(update.get("toolCallId"))
                if call_id in tool_calls:
                    tc = tool_calls[call_id]
                    tc.result_content += _extract_tool_update_text(update.get("content", ""))
                    _apply_tool_result_meta(tc, update)

    return call_order


def _extract_tool_update_text(content) -> str:
    """Pull display text out of a tool_call_update content payload."""
    if not content:
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            inner = item.get("content", {})
            if isinstance(inner, dict):
                parts.append(inner.get("text", "") or "")
            elif isinstance(inner, str):
                parts.append(inner)
        return "".join(parts)
    if isinstance(content, str):
        return content
    return ""


# Identity map: event_type == Grok sessionUpdate (1:1).
_MESSAGE_TYPE_MAP = {
    "user_message_chunk": "user_message_chunk",
    "agent_message_chunk": "agent_message_chunk",
    "agent_thought_chunk": "agent_thought_chunk",
}


def _extract_message_text(content) -> str:
    """Normalize a message chunk content payload to plain text."""
    if isinstance(content, dict) and content.get("type") == "text":
        return content.get("text", "")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(json.dumps(item))
        return "".join(parts)
    if isinstance(content, str):
        return content
    return json.dumps(content)


def _coalesce_tool_result(
    update: dict,
    ts: int | str | None,
    line_no: int,
    events: list[TraceEvent],
    idx: int,
    pending_tools: dict[str, TraceEvent],
    result_by_call: dict[str, int],
) -> int:
    """Coalesce streaming tool_call_update rows into a single tool_result event.

    Returns the (possibly incremented) event index.
    """
    epoch_ts = _as_epoch_ts(ts) if not isinstance(ts, int) else ts
    call_id = update.get("toolCallId", "")
    is_error = update.get("isError")
    status = update.get("status", "")
    result_text = _extract_tool_update_text(update.get("content", ""))
    # MCP and some host tools put the body only in rawOutput (content is null).
    if not result_text:
        result_text = _extract_raw_output_text(update.get("rawOutput"))
    failed = is_error is True or status == "failed"
    terminal = failed or status in ("completed", "failed")

    if not result_text and not failed and not terminal:
        return idx

    tool_name = ""
    if call_id in pending_tools:
        tool_name = pending_tools[call_id].tool_name
        if failed:
            pending_tools[call_id].is_error = True

    if call_id in result_by_call:
        ev = events[result_by_call[call_id]]
        if result_text and (len(result_text) >= len(ev.content or "") or terminal):
            ev.content = result_text
        if epoch_ts is not None:
            ev.timestamp = epoch_ts
        ev.update_index = line_no
        if failed:
            ev.is_error = True
        if tool_name and not ev.tool_name:
            ev.tool_name = tool_name
    elif result_text or failed:
        ev = TraceEvent(
            index=idx,
            event_type="tool_call_update",
            timestamp=epoch_ts,
            content=result_text,
            tool_call_id=call_id,
            tool_name=tool_name,
            is_error=failed,
            update_index=line_no,
        )
        result_by_call[call_id] = len(events)
        events.append(ev)
        idx += 1
    return idx


def parse_timeline(session_dir: Path) -> list[TraceEvent]:
    """Parse updates.jsonl (+ events.jsonl turn markers) into a linear timeline.

    Streaming ``tool_call_update`` events (e.g. every ``CC`` line from a long
    ``make``) are coalesced into a **single** ``tool_result`` row per
    ``toolCallId``.  Earlier versions appended one row per update, which made
    builds look like hundreds of separate terminal runs in the TUI.

    Runtime markers from ``events.jsonl`` (``turn_started`` / ``turn_ended`` /
    errors) are merged with update rows and **ordered by timestamp** (then
    original index) so multi-turn sessions do not pile all starts at the top
    and all ends at the bottom.

    Results are cached by :func:`session_trace_mtime` so live refresh that calls
    :func:`load_session_meta` (which needs ``num_events``) then ``parse_timeline``
    again does not re-read multi‑MB ``updates.jsonl`` twice per tick.
    """
    sd = Path(session_dir)
    cache_key = str(sd.resolve()) if sd.exists() else str(sd)
    mtime = session_trace_mtime(sd)
    cached = _timeline_cache.get(cache_key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    runtime_markers, _outcome, _loops = parse_runtime_markers(session_dir)

    updates_file = session_dir / "updates.jsonl"
    events: list[TraceEvent] = []
    idx = 0
    pending_tools: dict[str, TraceEvent] = {}
    # call_id -> position in `events` of the coalesced tool_result row
    result_by_call: dict[str, int] = {}

    if not updates_file.exists():
        for m in runtime_markers:
            m.index = idx
            events.append(m)
            idx += 1
        return _prepend_system_prompt(session_dir, _finalize_timeline_order(events))

    # Streaming tool_call_update lines often *are* the multi‑100MB file (cumulative
    # shell output). Skip full JSON parse unless the line looks terminal — the
    # completed/failed update carries the body we coalesce into one row.
    _TU = b"tool_call_update"
    _TERM = (
        b'"status":"completed"',
        b'"status": "completed"',
        b'"status":"failed"',
        b'"status": "failed"',
        b'"isError":true',
        b'"isError": true',
    )

    with open(updates_file, "rb") as f:
        for line_no, line in enumerate(f):
            if _TU in line and not any(m in line for m in _TERM):
                continue

            raw = json_object_line(line)

            if raw is None:
                continue

            params = raw.get("params")
            update_raw = params.get("update") if isinstance(params, dict) else None
            update: JsonObject = as_json_object(update_raw) if isinstance(update_raw, dict) else {}
            etype = str(update.get("sessionUpdate") or "")
            ts_raw = raw.get("timestamp")
            if ts_raw is None:
                ts_raw = raw.get("ts")
            ts = _as_epoch_ts(ts_raw if isinstance(ts_raw, (str, int, float)) else None)

            if etype in _MESSAGE_TYPE_MAP:
                content = _extract_message_text(update.get("content", ""))
                mapped = _MESSAGE_TYPE_MAP[etype]
                if events and events[-1].event_type == mapped:
                    events[-1].content += content
                else:
                    events.append(
                        TraceEvent(
                            index=idx,
                            event_type=mapped,
                            timestamp=ts,
                            content=content,
                            update_index=line_no,
                        )
                    )
                    idx += 1

            elif etype == "tool_call":
                call_id = json_as_str(update.get("toolCallId"))
                raw_input = update.get("rawInput", {})
                tool_name = resolve_tool_display_name(
                    json_as_str(update.get("title")) or "unknown",
                    ToolInputBag(raw_input) if isinstance(raw_input, dict) else None,
                )
                ev = TraceEvent(
                    index=idx,
                    event_type="tool_call",
                    timestamp=ts,
                    tool_name=tool_name,
                    tool_call_id=call_id,
                    raw_input=ToolInputBag(raw_input)
                    if isinstance(raw_input, dict)
                    else ToolInputBag(),
                    update_index=line_no,
                )
                events.append(ev)
                pending_tools[call_id] = ev
                idx += 1

            elif etype == "tool_call_update":
                idx = _coalesce_tool_result(
                    update,
                    ts,
                    line_no,
                    events,
                    idx,
                    pending_tools,
                    result_by_call,
                )

            elif etype == "plan":
                content = json.dumps(update.get("todos", update), indent=2)[:500]
                events.append(
                    TraceEvent(
                        index=idx,
                        event_type="plan",
                        timestamp=ts,
                        content=content,
                        update_index=line_no,
                    )
                )
                idx += 1

            elif etype in (
                "task_backgrounded",
                "task_completed",
                "turn_completed",
                "current_mode_update",
                "retry_state",
            ):
                # 1:1 Grok sessionUpdate → timeline row
                bits: list[str] = [etype]
                for key in (
                    "tool_call_id",
                    "task_id",
                    "command",
                    "cwd",
                    "prompt_id",
                    "mode",
                    "state",
                ):
                    val = update.get(key)
                    if val is not None and str(val).strip():
                        bits.append(f"{key}={val}")
                snap = update.get("task_snapshot")
                if isinstance(snap, dict) and snap:
                    bits.append(json.dumps(snap)[:400])
                events.append(
                    TraceEvent(
                        index=idx,
                        event_type=etype,
                        timestamp=ts,
                        content="  ".join(str(b) for b in bits),
                        tool_call_id=json_as_str(update.get("tool_call_id")),
                        update_index=line_no,
                    )
                )
                idx += 1

            elif etype == "subagent_spawned":
                desc = update.get("description", "")
                agent_type = update.get("subagentType", "")
                events.append(
                    TraceEvent(
                        index=idx,
                        event_type="subagent_spawned",
                        timestamp=ts,
                        update_index=line_no,
                        content=f"Spawned {agent_type}: {desc}",
                    )
                )
                idx += 1

            elif etype == "subagent_finished":
                events.append(
                    TraceEvent(
                        index=idx,
                        event_type="subagent_finished",
                        timestamp=ts,
                        update_index=line_no,
                        content="Subagent finished",
                    )
                )
                idx += 1

    for m in runtime_markers:
        m.index = idx
        events.append(m)
        idx += 1

    out = _prepend_system_prompt(session_dir, _finalize_timeline_order(events))
    _timeline_cache[cache_key] = (mtime, out)
    return out


def _is_turn_started_marker(ev: TraceEvent) -> bool:
    if ev.event_type == "turn_started":
        return True
    # Legacy timelines (pre Grok-aligned types)
    if ev.event_type in ("session", "session_error"):
        return "turn started" in (ev.content or "").lower()
    return False


def _is_turn_marker(ev: TraceEvent) -> bool:
    if ev.event_type in ("turn_started", "turn_ended", "turn_completed"):
        return True
    if ev.event_type in ("session", "session_error"):
        c = (ev.content or "").lower()
        return "turn started" in c or "turn ended" in c
    return False


def _is_substantive_timeline_event(ev: TraceEvent) -> bool:
    """True when the event is real agent activity (not a turn lifecycle marker)."""
    if _is_turn_marker(ev):
        return False
    return True


def _is_turn_ended_marker(ev: TraceEvent) -> bool:
    if ev.event_type == "turn_ended":
        return True
    if ev.event_type in ("session", "session_error"):
        return "turn ended" in (ev.content or "").lower()
    return False


def _drop_empty_turn_starts(events: list[TraceEvent]) -> list[TraceEvent]:
    """Remove ``turn started`` markers with no agent activity after them.

    Grok often emits a trailing ``turn_started`` when interactive mode opens the
    next turn (or the harness awaits follow-up) with no user/assistant/tools yet.
    That shows up as a stray final \"turn started\" on otherwise single-turn
    timelines. Keep starts that bracket real work. Keep a sole open
    ``turn_started`` when the session has not ended any turn yet (live / incomplete).
    """
    if not events:
        return events
    has_completed_turn = any(_is_turn_ended_marker(e) for e in events)
    drop: set[int] = set()
    n = len(events)
    for i, ev in enumerate(events):
        if not _is_turn_started_marker(ev):
            continue
        has_work = False
        for j in range(i + 1, n):
            nxt = events[j]
            if _is_turn_started_marker(nxt):
                break
            if _is_turn_marker(nxt):
                # ``turn ended`` with nothing in between → empty turn; drop start
                break
            if _is_substantive_timeline_event(nxt):
                has_work = True
                break
        if has_work:
            continue
        # Live session: only a start marker so far — keep it.
        if not has_completed_turn:
            continue
        drop.add(i)
    if not drop:
        return events
    return [ev for i, ev in enumerate(events) if i not in drop]


_system_prompt_cache: dict[str, tuple[float, str]] = {}


def load_system_prompt_text(session_dir: Path) -> str:
    """Return ``system_prompt.txt`` for the session, or empty if missing.

    Cached by path + mtime so live timeline reloads do not re-read multi‑KB
    prompts on every poll.
    """
    fp = Path(session_dir) / "system_prompt.txt"
    if not fp.is_file():
        return ""
    key = str(fp)
    try:
        mtime = fp.stat().st_mtime
    except OSError:
        return ""
    hit = _system_prompt_cache.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    _system_prompt_cache[key] = (mtime, text)
    return text


def _prepend_system_prompt(session_dir: Path, events: list[TraceEvent]) -> list[TraceEvent]:
    """Put the session system prompt first in the timeline when the file exists."""
    text = load_system_prompt_text(session_dir).strip()
    if not text:
        return events
    head = TraceEvent(index=0, event_type="system", content=text)
    out = [head, *events]
    for i, ev in enumerate(out):
        ev.index = i
    return out


def _finalize_timeline_order(events: list[TraceEvent]) -> list[TraceEvent]:
    """Sort by epoch timestamp (stable for ties), drop empty starts, reindex."""
    if not events:
        return events

    def _sort_key(ev: TraceEvent) -> tuple[int, int, int, int]:
        ts = ev.timestamp
        # Missing timestamps sort after dated events but keep relative order via index.
        ts_key = int(ts) if ts is not None else 2**62
        ui = ev.update_index if ev.update_index is not None else 10**9
        # Prefer turn_ended before turn_started on identical timestamps so a
        # completed turn closes before the next turn opens in the UI.
        kind = 1 if _is_turn_started_marker(ev) else 0
        return (ts_key, ui, kind, ev.index)

    ordered = sorted(events, key=_sort_key)
    ordered = _drop_empty_turn_starts(ordered)
    for i, ev in enumerate(ordered):
        ev.index = i
    return ordered


def parse_chat_history(session_dir: Path) -> list[ChatMessage]:
    """Parse chat_history.jsonl for message-level data."""
    chat_file = session_dir / "chat_history.jsonl"
    if not chat_file.exists():
        return []

    messages: list[ChatMessage] = []
    with open(chat_file) as f:
        for line in f:
            row = json_object_line(line)

            if row is None:
                continue
            if isinstance(row, dict):
                messages.append(row)  # type: ignore[arg-type]  # json.loads → dict; ChatMessage is TypedDict
    return messages


def extract_prompt(session_dir: Path) -> str:
    """Extract the user prompt from the chat history (the <user_query> block)."""
    messages = parse_chat_history(session_dir)
    for msg in messages:
        content = msg.get("content", "")
        texts: list[str] = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if isinstance(text, str):
                        texts.append(text)
        elif isinstance(content, str):
            texts = [content]
        for text in texts:
            start = text.find("<user_query>")
            if start < 0:
                continue
            end = text.find("</user_query>", start)
            if end < 0:
                continue
            return text[start + len("<user_query>") : end].strip()
    return ""


def session_trace_mtime(session_dir: Path) -> float:
    """Newest mtime among trace artifacts (0 if none)."""
    newest = 0.0
    for name in (
        "events.jsonl",
        "chat_history.jsonl",
        "updates.jsonl",
        "summary.json",
        "signals.json",
    ):
        fp = session_dir / name
        try:
            if fp.is_file():
                newest = max(newest, fp.stat().st_mtime)
        except OSError:
            continue
    if newest <= 0:
        try:
            newest = session_dir.stat().st_mtime
        except OSError:
            pass
    return newest


def _events_open_turn_after_completed(session_dir: Path) -> bool:
    """True when a later turn has started after at least one turn completed.

    That means the agent is **running** the next turn (not waiting for a
    follow-up prompt). Awaiting is only from the interactive turn gate.
    """
    events_file = session_dir / "events.jsonl"
    if not events_file.is_file():
        return False
    open_starts = 0
    ended = 0
    try:
        with events_file.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    et = (json_object_line(line) or {}).get("type")
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
                if et == "turn_started":
                    open_starts += 1
                elif et == "turn_ended":
                    open_starts = max(0, open_starts - 1)
                    ended += 1
    except OSError:
        return False
    return ended > 0 and open_starts > 0


def _infer_incomplete_turn_outcome(session_dir: Path) -> str:
    """Outcome when harness never wrote turn_ended.

    Live eval containers write traces incrementally; those sessions should show
    ``running``, not ``interrupted``. Only mark interrupted when an explicit
    marker exists, or trace data is present but has gone stale.
    """
    marker_path = session_dir / INTERRUPTED_MARKER_FILENAME
    if marker_path.is_file():
        return "interrupted"

    has_body = any(
        (session_dir / n).is_file() and (session_dir / n).stat().st_size > 200
        for n in ("events.jsonl", "chat_history.jsonl", "updates.jsonl")
    )
    if not has_body:
        return ""

    mtime = session_trace_mtime(session_dir)
    if mtime <= 0:
        return "interrupted"

    age = datetime.now(UTC).timestamp() - mtime
    if age < INCOMPLETE_STALE_SECONDS:
        return "running"
    return "interrupted"


def _load_summary(meta: SessionMeta, session_dir: Path) -> None:
    """Populate meta from summary.json."""
    summary_file = session_dir / "summary.json"
    if not summary_file.exists():
        return
    try:
        with open(summary_file) as f:
            data = json.load(f)
        meta.model_id = data.get("current_model_id", "unknown")
        meta.title = data.get("generated_title", "") or data.get("session_summary", "")
        meta.summary_text = data.get("session_summary", "")
        meta.created_at = data.get("created_at", "")
        meta.updated_at = data.get("updated_at", "")
        meta.num_messages = data.get("num_messages", 0)
        info = data.get("info", {})
        if info:
            meta.git_repo = info.get("git_repo_url", "")
            meta.git_branch = info.get("git_branch", "")
    except (json.JSONDecodeError, KeyError):
        pass


def _load_signals(meta: SessionMeta, session_dir: Path) -> None:
    """Populate meta from signals.json."""
    signals_file = session_dir / "signals.json"
    if not signals_file.exists():
        return
    try:
        with open(signals_file) as f:
            sig = json.load(f)
        meta.tool_call_count = sig.get("toolCallCount", 0)
        meta.tool_failure_count = sig.get("toolFailureCount", 0)
        meta.error_count = sig.get("errorCount", 0)
        meta.doom_loop_warnings = sig.get("doomLoopWarnings", 0)
        meta.duration_seconds = sig.get("sessionDurationSeconds", 0)
        meta.lines_added = sig.get("agentLinesAdded", 0)
        meta.lines_removed = sig.get("agentLinesRemoved", 0)
    except (json.JSONDecodeError, KeyError):
        pass


def _load_run_meta(meta: SessionMeta, session_dir: Path) -> None:
    """Populate meta from run.json (batch / runner metadata)."""

    run_json = session_dir / "run.json"
    if not run_json.exists():
        for ancestor in session_dir.parents:
            if is_run_dir_name(ancestor.name):
                run_json = ancestor / "run.json"
                break
            if ancestor.name == "traces":
                break

    if run_json.exists():
        try:
            with open(run_json) as f:
                run_data = json.load(f)
            meta.run_id = run_data.get("run_id", "")
            meta.task_id = run_data.get("task_id", "")
            if not meta.git_repo:
                meta.git_repo = run_data.get("repo_url", "")
            if not meta.git_branch:
                meta.git_branch = run_data.get("repo_branch", "")
            resolved = _model_from_run_json(session_dir, run_data)
            if resolved:
                from .runs.batch import split_model_effort

                mid, eff = split_model_effort(resolved)
                meta.model_id = mid or resolved
                if eff:
                    meta.reasoning_effort = eff
        except (json.JSONDecodeError, KeyError):
            pass

    # No run.json: try groket-{run_id}-{model} parent folder suffix
    if not meta.model_id or meta.model_id in ("unknown", "v9", "grok-build"):
        inferred = _model_from_run_parent(session_dir)
        if inferred and inferred not in ("unknown",) and inferred != meta.model_id:
            if meta.model_id in ("unknown", "v9") or len(inferred) > len(meta.model_id):
                meta.model_id = inferred

    # Effort from container/run dir name (…-high, …-xhigh, …-max) or config.toml.
    if not meta.reasoning_effort:
        meta.reasoning_effort = _reasoning_effort_from_run_dir(session_dir)
    if not meta.reasoning_effort:
        meta.reasoning_effort = _reasoning_effort_from_run_config(session_dir)


def load_session_meta(
    session_dir: Path,
    *,
    include_timeline_count: bool = True,
    timeline_count: int | None = None,
) -> SessionMeta:
    """Load session metadata from trace artifacts and run.json.

    :param include_timeline_count: When True (default) and *timeline_count* is
        None, set ``num_events`` via :func:`parse_timeline` (coalesced length,
        mtime-cached). Set False for fast list loads; pass *timeline_count*
        when a trusted cached value is available for the current trace mtime.
    :param timeline_count: Explicit coalesced event count (skip parse when set).
    """
    session_id = session_dir.name

    meta = SessionMeta(session_id=session_id, session_dir=session_dir)

    _load_summary(meta, session_dir)
    _load_signals(meta, session_dir)

    # events.jsonl — turn/loop outcome (harness-level; not in updates.jsonl)
    _markers, turn_outcome, loop_count = parse_runtime_markers(session_dir)
    if turn_outcome:
        meta.turn_outcome = turn_outcome
    if loop_count:
        meta.loop_count = loop_count

    # Incomplete / in-progress: no turn_ended yet (live jobs vs killed runs)
    if not meta.turn_outcome:
        inferred = _infer_incomplete_turn_outcome(session_dir)
        if inferred:
            meta.turn_outcome = inferred

    # Interactive gate overrides while the eval is open. Awaiting only when the
    # gate is awaiting_follow_up. Host ``command=done`` means *finishing* only
    # while traces are still fresh; if the container never rewrote ``state=done``
    # (removed / crashed), settle to completed rather than leave ``running``.
    try:
        from .session.turn_gate import (
            host_requested_done,
            read_turn_gate_status,
            session_awaits_follow_up,
        )

        gst = read_turn_gate_status(session_dir)
        gstate = str(gst.get("state") or "")
        marker_outcome = (meta.turn_outcome or "").strip()
        live_outcomes = frozenset({"", "running", "in_progress", "pending", "awaiting_follow_up"})
        if host_requested_done(session_dir) and gstate != "done":
            if _infer_incomplete_turn_outcome(session_dir) == "running":
                meta.turn_outcome = "running"
            elif marker_outcome and marker_outcome not in live_outcomes:
                meta.turn_outcome = marker_outcome
            else:
                meta.turn_outcome = "completed"
        elif session_awaits_follow_up(session_dir):
            meta.turn_outcome = "awaiting_follow_up"
        elif gstate == "running":
            meta.turn_outcome = "running"
        elif gstate == "done":
            if not marker_outcome or marker_outcome in live_outcomes:
                meta.turn_outcome = "completed"
        elif _events_open_turn_after_completed(session_dir):
            meta.turn_outcome = "running"
    except Exception:
        logger.debug("turn gate status for %s", session_dir, exc_info=True)
        if _events_open_turn_after_completed(session_dir):
            meta.turn_outcome = "running"

    if meta.turn_failed and not meta.error_count:
        # Surface harness failure even when signals.json tool errors are zero
        meta.error_count = max(meta.error_count, 1)

    # Events column = coalesced timeline length (same as the browser). Prefer an
    # explicit count (mtime-validated cache) so list loads do not re-parse every
    # multi‑MB updates.jsonl on launch.
    if timeline_count is not None:
        meta.num_events = max(0, int(timeline_count))
    elif include_timeline_count:
        try:
            meta.num_events = len(parse_timeline(session_dir))
        except Exception:
            logger.debug("Failed to count timeline events for %s", session_dir, exc_info=True)
            meta.num_events = 0
    else:
        meta.num_events = 0

    _load_run_meta(meta, session_dir)

    return meta


def list_turn_outcome_for_dir(session_dir: Path) -> str:
    """Live-only turn status for the sessions list poll (gate + freshness).

    Returns ``running`` / ``awaiting_follow_up`` / ``""``. Does **not** return
    ``interrupted`` — that inference is for full :func:`load_session_meta` only
    (overwriting finished sessions with interrupted made the list show
    "cancelled" for old successful runs).
    """
    sd = Path(session_dir)
    try:
        from .session.turn_gate import (
            host_requested_done,
            read_turn_gate_status,
            session_awaits_follow_up,
        )

        gst = read_turn_gate_status(sd)
        gstate = str(gst.get("state") or "")
        if host_requested_done(sd) and gstate != "done":
            # Finishing only while traces are fresh; else container is gone.
            if _infer_incomplete_turn_outcome(sd) == "running":
                return "running"
            return ""
        if session_awaits_follow_up(sd):
            return "awaiting_follow_up"
        if gstate == "running":
            return "running"
        if gstate == "done":
            return ""
    except Exception:
        logger.debug("list turn outcome gate for %s", sd, exc_info=True)
    # Only report running while traces are still fresh; never interrupted here.
    inferred = _infer_incomplete_turn_outcome(sd)
    if inferred == "running":
        return "running"
    return ""


def _find_container_for_session(
    session_dir: Path,
    sessions_map: dict,
) -> str:
    """Match a session directory to its container name from run.json sessions map."""

    sd_res: Path
    try:
        sd_res = session_dir.resolve()
    except OSError:
        sd_res = session_dir

    sid = session_dir.name

    for cname, spath in sessions_map.items():
        try:
            p = Path(str(spath)).expanduser()
            try:
                p_res = p.resolve()
            except OSError:
                p_res = p
            if p_res == sd_res or sid == p.name:
                return str(cname)
            try:
                if sd_res.is_relative_to(p_res) or p_res.is_relative_to(sd_res):
                    return str(cname)
            except (ValueError, AttributeError):
                pass
            if sid in str(spath):
                return str(cname)
        except (OSError, ValueError, TypeError):
            continue

    # Walk parents for groket-* container dir name
    for anc in [session_dir, *session_dir.parents]:
        if is_run_dir_name(anc.name):
            return anc.name
    return ""


def _model_from_run_json(session_dir: Path, run_data: dict) -> str:
    """Map this session to the model launched for its groket-* container.

    Returns a bare model id or ``model:effort`` launch token when the recipe
    stored effort-qualified models.
    """

    models = [str(m) for m in (run_data.get("models") or []) if m]
    sessions_map = run_data.get("sessions") or {}
    if not models and not sessions_map:
        return ""

    matched = _find_container_for_session(session_dir, sessions_map)
    if not matched:
        return ""

    if models:
        picked = _match_model_to_container(matched, models)
        if picked:
            return picked

    # Fall back: suffix after run_id segment in groket-{run_id}-{suffix}
    run_id = str(run_data.get("run_id") or "")
    if run_id:
        for pfx in RUN_PREFIXES:
            head = f"{pfx}{run_id}-"
            if matched.startswith(head):
                suffix = re.sub(r"-\d+$", "", matched[len(head) :])
                if suffix:
                    return suffix
    return ""


def _match_model_to_container(container_name: str, models: list[str]) -> str:
    """Match container name to a launch token (bare model or ``model:effort``).

    Runner names containers ``groket-{run_id}-{modelTail}`` or
    ``groket-{run_id}-{modelTail}-{effortPrefix}`` when effort is set.
    """
    from .runs.batch import split_model_effort

    cname = container_name.lower()
    best = ""
    best_score = 0
    for model in models:
        token = model.strip()
        if not token:
            continue
        mid, effort = split_model_effort(token)
        m = mid or token
        short = m.split("-")[-1][:10].lower()
        full_l = m.lower()
        score = 0
        if cname.endswith(short) or f"-{short}" in cname:
            score = 10 + len(short)
        elif short and short in cname:
            score = 5 + len(short)
        elif full_l in cname:
            score = 8 + len(full_l)
        # Also match v9-bottlerocket style ids against container ...-bottlerock
        if m.startswith("v9-") and short:
            alias = m[3:]
            alias_short = alias[:10].lower()
            if cname.endswith(alias_short) or f"-{alias_short}" in cname:
                score = max(score, 12 + len(alias_short))
        # Prefer effort-qualified tokens when the container embeds the effort tag
        if effort and effort[:4].lower() in cname:
            score += 20
        elif effort:
            # Slight preference for qualified tokens over bare when scores tie
            score += 1
        if score > best_score:
            best_score = score
            best = token
    return best


def _reasoning_effort_from_run_dir(session_dir: Path) -> str:
    """Infer effort from a ``groket-{run_id}-{slug}`` parent (effort suffix in slug)."""
    from .runs.batch import REASONING_EFFORTS

    # Longest names first so ``xhigh`` wins over ``high``.
    efforts = sorted(REASONING_EFFORTS, key=len, reverse=True)
    for anc in [session_dir, *session_dir.parents]:
        if not is_run_dir_name(anc.name):
            if anc.name == "traces":
                break
            continue
        name = anc.name.lower()
        for eff in efforts:
            if name.endswith(f"-{eff}"):
                return eff
        # Container slugs may truncate (e.g. ``…-xhig``); match effort prefixes.
        for eff in efforts:
            prefix = eff[:4] if len(eff) >= 4 else eff
            if prefix and (name.endswith(f"-{prefix}") or f"-{prefix}-" in name):
                # Only accept when the prefix uniquely identifies one effort.
                hits = [e for e in efforts if e.startswith(prefix) or e[:4] == prefix]
                if len(hits) == 1:
                    return hits[0]
        break
    return ""


def _reasoning_effort_from_run_config(session_dir: Path) -> str:
    """Read ``default_reasoning_effort`` from a run ``*config.toml`` if present."""
    from .runs.batch import REASONING_EFFORTS

    names = ("gte-config.toml", "groket-config.toml", "config.toml")
    candidates: list[Path] = [session_dir / n for n in names]
    for ancestor in session_dir.parents:
        for n in names:
            candidates.append(ancestor / n)
        if ancestor.name == "traces" or is_run_dir_name(ancestor.name):
            # Include the run dir itself, then stop climbing past traces
            if ancestor.name == "traces":
                break
    seen: set[Path] = set()
    for fp in candidates:
        try:
            key = fp.resolve()
        except OSError:
            key = fp
        if key in seen or not fp.is_file():
            continue
        seen.add(key)
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("default_reasoning_effort"):
                continue
            if "=" not in stripped:
                continue
            val = stripped.split("=", 1)[1].strip().strip("\"'")
            if val.lower() in REASONING_EFFORTS:
                return val.lower()
    return ""


def _model_from_run_parent(session_dir: Path) -> str:

    for anc in [session_dir, *session_dir.parents]:
        name = anc.name
        if not is_run_dir_name(name):
            continue
        # groket-{12hex}-{model_suffix} or groket-{task}-{model_short}
        body = strip_run_prefix(name)
        parts = body.split("-")
        if len(parts) >= 2:
            # Prefer last segment(s) as model tag (may be truncated)
            suffix = parts[-1]
            if suffix.isdigit() and len(parts) >= 3:
                suffix = parts[-2]
            if suffix and suffix not in ("build", "traces", "workspace"):
                return suffix
        break
    return ""


# Host staging / noise under traces/ — never real Grok session dirs.
_SKIP_SESSION_WALK_DIRS = frozenset(
    {
        "groket-plugins",
        "groket-skills",
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
    }
)


def _prune_session_walk_dirs(dirnames: list[str]) -> None:
    """In-place: do not descend into eval staging or VCS noise.

    Container dirs are named ``groket-<id>-<model>`` and **must** be walked;
    only explicit staging folder names are skipped.
    """
    kept: list[str] = []
    for d in dirnames:
        if d in _SKIP_SESSION_WALK_DIRS:
            continue
        # Sibling stage dir: traces/groket-foo-model.stage/
        if d.endswith(".stage"):
            continue
        kept.append(d)
    dirnames[:] = kept


def find_sessions(root: Path) -> list[Path]:
    """Recursively find session directories.

    A session directory is identified by updates.jsonl / summary.json (stable)
    or a non-empty events.jsonl (live mid-run).

    Skips eval staging trees (``groket-plugins``, ``groket-skills``, ``*.stage``)
    so large marketplace checkouts do not stall the session list.
    """
    sessions: list[Path] = []
    if not root.exists():
        return sessions
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        _prune_session_walk_dirs(dirnames)
        names = set(filenames)
        path = Path(dirpath)
        if names & {"updates.jsonl", "summary.json"}:
            sessions.append(path)
        elif "events.jsonl" in names:
            try:
                if (path / "events.jsonl").stat().st_size > 0:
                    sessions.append(path)
            except OSError:
                pass
    return sessions
