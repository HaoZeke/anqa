"""Trace parser — reads session directories into structured data."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from .constants import INCOMPLETE_STALE_SECONDS, INTERRUPTED_MARKER_FILENAME
from .models import ChatMessage, SessionMeta, ToolCall, ToolInputBag, TraceEvent
from .paths import RUN_PREFIXES, is_run_dir_name, strip_run_prefix

logger = logging.getLogger(__name__)


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
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
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
                            event_type="session",
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
                            event_type="session_error" if is_err else "session",
                            timestamp=ts,
                            content=body,
                            is_error=is_err,
                            update_index=line_no,
                        )
                    )

                elif et == "loop_started":
                    try:
                        loop_count = max(loop_count, int(ev.get("loop_index", 0)) + 1)
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


def _apply_tool_result_meta(tc: ToolCall, update: dict) -> None:
    """Apply rawOutput metadata and error status from a tool_call_update."""
    raw_output = update.get("rawOutput")
    if isinstance(raw_output, dict):
        ofp = raw_output.get("output_for_prompt", "")
        if ofp and not tc.result_content:
            tc.result_content = ofp
        elif ofp and ofp != tc.result_content:
            if ofp.startswith("exit:") and not tc.result_content.startswith("exit:"):
                tc.result_content = ofp
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
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            update = event.get("params", {}).get("update", {})
            event_type = update.get("sessionUpdate", "")
            timestamp = event.get("timestamp")

            if event_type == "tool_call":
                call_id = update.get("toolCallId", "")
                tool_name = update.get("title", "") or "unknown"
                raw_input = update.get("rawInput", {})
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
                call_id = update.get("toolCallId", "")
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


_MESSAGE_TYPE_MAP = {
    "user_message_chunk": "user",
    "agent_message_chunk": "assistant",
    "agent_thought_chunk": "thought",
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
        if result_text and (len(result_text) >= len(ev.content) or terminal):
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
            event_type="tool_result",
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
    """
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
        return _finalize_timeline_order(events)

    with open(updates_file) as f:
        for line_no, line in enumerate(f):
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            update = raw.get("params", {}).get("update", {})
            etype = update.get("sessionUpdate", "")
            ts = _as_epoch_ts(raw.get("timestamp") or raw.get("ts"))

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
                call_id = update.get("toolCallId", "")
                tool_name = update.get("title", "") or "unknown"
                raw_input = update.get("rawInput", {})
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

            elif etype == "subagent_spawned":
                desc = update.get("description", "")
                agent_type = update.get("subagentType", "")
                events.append(
                    TraceEvent(
                        index=idx,
                        event_type="subagent",
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
                        event_type="subagent",
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

    return _finalize_timeline_order(events)


def _is_turn_started_marker(ev: TraceEvent) -> bool:
    if ev.event_type not in ("session", "session_error"):
        return False
    return "turn started" in (ev.content or "").lower()


def _is_turn_marker(ev: TraceEvent) -> bool:
    if ev.event_type not in ("session", "session_error"):
        return False
    c = (ev.content or "").lower()
    return "turn started" in c or "turn ended" in c


def _is_substantive_timeline_event(ev: TraceEvent) -> bool:
    """True when the event is real agent activity (not a turn lifecycle marker)."""
    if _is_turn_marker(ev):
        return False
    # Keep non-turn session rows (errors, etc.) as substantive so we do not
    # drop a start that only has an error after it.
    return True


def _is_turn_ended_marker(ev: TraceEvent) -> bool:
    if ev.event_type not in ("session", "session_error"):
        return False
    return "turn ended" in (ev.content or "").lower()


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
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
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


def _events_awaiting_next_turn(session_dir: Path) -> bool:
    """True when events.jsonl has a ``turn_started`` not closed by a later ``turn_ended``.

    Interactive / multi-turn sessions often open the next turn after the previous
    completed; the harness outcome stays ``completed`` for the last *ended* turn
    even though the session is waiting for more input.
    """
    events_file = session_dir / "events.jsonl"
    if not events_file.is_file():
        return False
    open_starts = 0
    try:
        with events_file.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    et = json.loads(line).get("type")
                except json.JSONDecodeError:
                    continue
                if et == "turn_started":
                    open_starts += 1
                elif et == "turn_ended":
                    open_starts = max(0, open_starts - 1)
    except OSError:
        return False
    return open_starts > 0


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

    # Effort from eval config.toml when not on the launch token (older runs).
    if not meta.reasoning_effort:
        meta.reasoning_effort = _reasoning_effort_from_run_config(session_dir)


def load_session_meta(session_dir: Path) -> SessionMeta:
    """Load session metadata from trace artifacts and run.json."""
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

    # Interactive multi-turn gate overrides harness outcome while the eval is open.
    try:
        from .session.turn_gate import read_turn_gate_status, session_awaits_follow_up

        if session_awaits_follow_up(session_dir):
            meta.turn_outcome = "awaiting_follow_up"
        else:
            gst = read_turn_gate_status(session_dir)
            gstate = str(gst.get("state") or "")
            # Mid-turn in container (quit TUI and reopen — still live / resumable).
            if gstate == "running":
                meta.turn_outcome = "running"
            elif gstate == "done":
                pass  # keep harness outcome (usually completed)
            elif _events_awaiting_next_turn(session_dir):
                # Extra turn_started after last turn_ended (no gate file / gate lag).
                meta.turn_outcome = "awaiting_follow_up"
    except Exception:
        logger.debug("turn gate status for %s", session_dir, exc_info=True)
        if _events_awaiting_next_turn(session_dir):
            meta.turn_outcome = "awaiting_follow_up"

    if meta.turn_failed and not meta.error_count:
        # Surface harness failure even when signals.json tool errors are zero
        meta.error_count = max(meta.error_count, 1)

    # Timeline length (same coalesced stream as the browser) — not a file-size guess.
    # Raw updates.jsonl line counts over-count streaming tool_call_update chunks.
    try:
        meta.num_events = len(parse_timeline(session_dir))
    except Exception:
        logger.debug("Failed to count timeline events for %s", session_dir, exc_info=True)
        meta.num_events = 0

    _load_run_meta(meta, session_dir)

    return meta


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


def _reasoning_effort_from_run_config(session_dir: Path) -> str:
    """Read ``default_reasoning_effort`` from the eval run's config.toml if present."""
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
