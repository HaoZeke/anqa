"""Wire-shaped session views for the control plane (HUD / web / editors).

Pure domain loaders → JSON-RPC payloads. No Textual. Used by
:class:`~groket.integrations.control.ControlServer` handlers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import event_types as et
from ..models import JsonObject, JsonValue, SessionMeta, TraceEvent, as_json_object
from ..notes import notes_snapshot
from ..parser import load_session_meta, parse_timeline
from ..session.sources import classify_session_origin, work_traces_root
from ..session.tagged_blocks import unwrap_for_display
from ..session.turns import (
    TurnSegment,
    event_display_turn_map,
    harness_user_chrome_heading,
    is_operator_user_event,
    operator_prompt_text,
    segment_timeline_turns,
    turn_index_for_event,
)
from ..session.usage_stats import SessionUsageStats, collect_session_usage
from .catalog import session_catalog_row

DEFAULT_FINDINGS_LIMIT = 80

# Tool families aligned with ``ui.styles.tool_family`` (domain copy — no UI import).
_TOOL_FAMILY_READ = frozenset(
    {
        "read_file",
        "grep",
        "list_dir",
        "web_search",
        "read_resource",
        "list_resources",
    }
)
_TOOL_FAMILY_WRITE = frozenset(
    {
        "search_replace",
        "write_file",
        "create_file",
        "todo_write",
        "update_goal",
        "image_gen",
        "image_edit",
        "image_to_video",
        "reference_to_video",
    }
)
_TOOL_FAMILY_SHELL = frozenset(
    {
        "run_terminal_command",
        "get_command_or_subagent_output",
        "kill_command_or_subagent",
        "wait_commands_or_subagents",
        "monitor",
        "scheduler_create",
        "scheduler_delete",
        "scheduler_list",
    }
)
_TOOL_FAMILY_AGENT = frozenset(
    {
        "spawn_subagent",
        "ask_user_question",
        "enter_plan_mode",
        "exit_plan_mode",
        "use_tool",
        "search_tool",
        "call_mcp",
        "search_mcp",
    }
)


def tool_family(name: str) -> str:
    """Map a tool name to read | write | shell | agent | mcp | other."""
    n = (name or "").strip()
    if "__" in n or n.startswith("mcp_"):
        return "mcp"
    if n in _TOOL_FAMILY_READ:
        return "read"
    if n in _TOOL_FAMILY_WRITE:
        return "write"
    if n in _TOOL_FAMILY_SHELL:
        return "shell"
    if n in _TOOL_FAMILY_AGENT:
        return "agent"
    low = n.lower()
    if any(k in low for k in ("read", "get", "list", "search", "grep", "find")):
        return "read"
    if any(k in low for k in ("write", "edit", "create", "update", "delete", "save")):
        return "write"
    if any(k in low for k in ("run", "shell", "exec", "kill", "wait")):
        return "shell"
    return "other"


logger = logging.getLogger(__name__)

DEFAULT_TIMELINE_LIMIT = 300
MAX_TIMELINE_LIMIT = 2000
DEFAULT_CONTENT_CHARS = 4000
MAX_CONTENT_CHARS = 50_000


def session_meta_mapping(
    meta: SessionMeta,
    *,
    path: Path | None = None,
    origin: str | None = None,
) -> JsonObject:
    """Serialize :class:`SessionMeta` for ``session/get`` / enriched list rows."""
    try:
        path_str = str((path or meta.session_dir).resolve())
    except OSError:
        path_str = str(path or meta.session_dir)
    origin_key = (origin or meta.origin or "work").strip() or "work"
    return {
        "sessionId": (meta.session_id or meta.session_dir.name).strip(),
        "path": path_str,
        "title": meta.title or "",
        "summary": meta.summary_text or "",
        "label": meta.label,
        "model": meta.model_display,
        "modelId": meta.model_id or "",
        "reasoningEffort": meta.reasoning_effort or "",
        "status": meta.list_status_label(),
        "outcome": meta.turn_outcome or "",
        "origin": origin_key,
        "createdAt": meta.created_at or "",
        "updatedAt": meta.updated_at or "",
        "numMessages": int(meta.num_messages or 0),
        "numEvents": int(meta.num_events or 0),
        "durationSeconds": float(meta.duration_seconds or 0),
        "duration": meta.duration_str,
        "toolCallCount": int(meta.tool_call_count or 0),
        "toolFailureCount": int(meta.tool_failure_count or 0),
        "errorCount": int(meta.error_count or 0),
        "doomLoopWarnings": int(meta.doom_loop_warnings or 0),
        "linesAdded": int(meta.lines_added or 0),
        "linesRemoved": int(meta.lines_removed or 0),
        "contextWindowUsagePct": meta.context_window_usage_pct,
        "contextTokensUsed": meta.context_tokens_used,
        "contextWindowTokens": meta.context_window_tokens,
        "contextUsage": meta.context_usage_str,
        "contextUsageCompact": meta.context_usage_compact,
        "compactionCount": int(meta.compaction_count or 0),
        "gitRepo": meta.git_repo or "",
        "gitBranch": meta.git_branch or "",
        "gitCommit": meta.git_commit or "",
        "taskId": meta.task_id or "",
        "runId": meta.run_id or "",
        "loopCount": int(meta.loop_count or 0),
        "turnInProgress": bool(meta.turn_in_progress),
        "turnFailed": bool(meta.turn_failed),
    }


def timeline_event_mapping(
    event: TraceEvent,
    *,
    content_chars: int = DEFAULT_CONTENT_CHARS,
    turn_index: int | None = None,
) -> JsonObject:
    """Serialize one timeline event for ``session/timeline`` / overview.

    Includes ``kind`` / ``toolFamily`` so palette clients can color and unpack
    the same way as the TUI without re-implementing taxonomy. Optional
    *turn_index* is the sequential operator turn id (0-based) for this event.
    """
    cap = max(0, min(int(content_chars), MAX_CONTENT_CHARS))
    content_raw = event.content if isinstance(event.content, str) else str(event.content or "")
    # Strip outer harness tags for display (keep raw length for truncation meta).
    content = unwrap_for_display(content_raw)
    truncated = len(content) > cap
    body = content[:cap] if cap else ""
    raw: JsonValue = {}
    try:
        bag = event.raw_input
        if isinstance(bag, dict):
            raw = as_json_object(bag)
        elif hasattr(bag, "raw"):
            inner = bag.raw()
            if isinstance(inner, dict):
                raw = as_json_object(inner)
    except Exception:
        raw = {}
    kind = et.event_kind(event.event_type)
    tname = (event.tool_name or "").strip()
    family = tool_family(tname) if kind in ("tool", "tool_result") or tname else ""
    chrome_heading = (
        harness_user_chrome_heading(content_raw)
        if kind == "user" or event.event_type in et.USER_TYPES
        else None
    )
    # Harness injects system-reminder / background-task bodies as user_message_chunk;
    # re-label so TUI/HUD do not present them as operator "User" rows.
    if chrome_heading is not None:
        kind = "system"
    # Prefer structured tool headline when available.
    if kind == "tool" and tname:
        heading = tname if not family else f"{tname}"
    elif kind == "tool_result" and tname:
        heading = f"{tname} result"
    elif chrome_heading is not None:
        heading = chrome_heading
    elif kind == "user":
        heading = "User"
    elif kind == "agent":
        heading = "Assistant"
    elif kind == "thought":
        heading = "Thought"
    elif kind == "error":
        heading = "Error"
    elif kind == "system":
        heading = "System"
    else:
        heading = event.type_label
    type_label = chrome_heading.lower() if chrome_heading else event.type_label
    preview = body.split("\n", 1)[0][:200] if body else event.summary_line
    return {
        "index": int(event.index),
        "type": event.event_type or "",
        "typeLabel": type_label,
        "kind": kind,
        "toolFamily": family,
        "heading": heading,
        "harnessChrome": chrome_heading is not None,
        "timestamp": event.timestamp,
        "time": event.time_str,
        "content": body,
        "contentTruncated": truncated,
        "contentLength": len(content),
        "toolName": tname,
        "toolCallId": event.tool_call_id or "",
        "isError": bool(event.is_error),
        "updateIndex": int(event.update_index or 0),
        "promptIndex": event.prompt_index,
        "turnIndex": int(turn_index) if turn_index is not None else None,
        "preview": preview,
        "rawInput": raw,
    }


def _turn_user_prompt_preview(
    seg: TurnSegment,
    *,
    max_chars: int = 320,
) -> tuple[str, int | None]:
    """First *operator* user message text in *seg* and its timeline index.

    Used by HUD / editors as the turn card summary (what the user asked).
    Prefer nested ``<user_query>`` body; skip harness chrome tags.
    """
    for event in seg.events:
        if not is_operator_user_event(event):
            continue
        text = operator_prompt_text(event.content or "", max_chars=max_chars)
        if not text:
            continue
        return text, int(event.index)
    return "", None


def turn_segment_mapping(seg: TurnSegment, *, include_event_indexes: bool = True) -> JsonObject:
    """Serialize one turn segment for ``session/turns`` / overview turns."""
    summary, user_index = _turn_user_prompt_preview(seg)
    row: JsonObject = {
        "turnIndex": int(seg.turn_index),
        "turnNumber": seg.turn_number,
        "promptIndex": seg.prompt_index,
        "outcome": seg.outcome or "",
        "open": bool(seg.open),
        "label": seg.label,
        "summary": summary,
        "userEventIndex": user_index,
        "eventCount": int(seg.event_count),
        "toolCallCount": int(seg.tool_call_count),
        "toolErrorCount": int(seg.tool_error_count),
        "userCount": int(seg.user_count),
        "assistantCount": int(seg.assistant_count),
        "errorEventCount": int(seg.error_event_count),
        "firstIndex": seg.first_index,
        "lastIndex": seg.last_index,
        "durationSeconds": seg.duration_seconds(),
    }
    if include_event_indexes:
        row["eventIndexes"] = [int(e.index) for e in seg.events]
    return row


def usage_stats_mapping(usage: SessionUsageStats) -> JsonObject:
    """Compact usage summary for ``session/usage``."""
    host: list[JsonValue] = [
        {
            "name": t.name,
            "calls": int(t.calls),
            "errors": int(t.errors),
            "category": t.category,
        }
        for t in (usage.host_tools or usage.tools or [])[:40]
    ]
    mcp: list[JsonValue] = [
        {
            "serverId": s.server_id,
            "useToolCalls": int(s.use_tool_calls),
            "errors": int(s.errors),
            "configured": bool(s.configured),
        }
        for s in (usage.mcp_servers or [])[:40]
    ]
    skills: list[JsonValue] = [
        {
            "skillId": s.skill_id,
            "skillMdReads": int(s.skill_md_reads),
            "nameInTranscript": bool(s.name_in_transcript),
            "engaged": bool(s.engaged),
            "configured": bool(s.configured),
        }
        for s in (usage.skills or [])[:40]
    ]
    tools_invoked: list[JsonValue] = [
        str(x) for x in (getattr(usage, "mcp_tools_invoked", None) or [])[:40]
    ]
    return {
        "hostTools": host,
        "mcpServers": mcp,
        "skills": skills,
        "mcpBridgeCalls": int(getattr(usage, "mcp_bridge_calls", 0) or 0),
        "mcpToolsInvoked": tools_invoked,
    }


def _session_origin(session_dir: Path, work_dir: Path | None) -> str:
    from .sources import is_under_host_grok_sessions

    sd = Path(session_dir)
    if work_dir is not None:
        return classify_session_origin(sd, work_traces=work_traces_root(work_dir))
    if is_under_host_grok_sessions(sd):
        return "host"
    return "work"


def build_session_get(
    session_dir: Path,
    *,
    work_dir: Path | None = None,
    include_notes_revision: bool = True,
    include_timeline_count: bool = False,
) -> JsonObject:
    """Full ``session/get`` payload for *session_dir*.

    *include_timeline_count* defaults False so HUD-style clients stay fast
    (avoid a full ``parse_timeline`` just for the events column).
    """
    sd = Path(session_dir)
    origin = _session_origin(sd, work_dir)
    meta = load_session_meta(sd, include_timeline_count=include_timeline_count)
    meta.origin = origin
    out = session_meta_mapping(meta, path=sd, origin=origin)
    cat = session_catalog_row(sd, origin=origin)
    if cat is not None:
        out["catalog"] = cat
    if include_notes_revision:
        try:
            snap = notes_snapshot(sd)
            out["notesRevision"] = snap.revision
            out["notesCount"] = len(snap.doc.notes)
        except Exception:
            logger.debug("notes snapshot for session/get %s", sd, exc_info=True)
            out["notesRevision"] = ""
            out["notesCount"] = 0
    return out


def _finding_turn_indices(
    segs: list[TurnSegment],
    event_indices: list[int],
) -> list[int]:
    """Map finding event indices → sequential operator turn indices (unique, order preserved)."""
    out: list[int] = []
    seen: set[int] = set()
    for ei in event_indices:
        ti = turn_index_for_event(segs, int(ei))
        if ti is None or ti in seen:
            continue
        seen.add(ti)
        out.append(ti)
    return out


def finding_mapping(
    finding: object,
    *,
    segs: list[TurnSegment],
    plugin_id: str = "",
) -> JsonObject:
    """Serialize one analysis :class:`~groket.analysis.base.Finding` for palette clients."""
    # duck-typed to avoid hard import cycles in type checkers; runtime uses Finding.
    fid = str(getattr(finding, "id", "") or "")
    plug = str(getattr(finding, "plugin_id", "") or plugin_id or "")
    sev = getattr(finding, "severity", None)
    if sev is not None and hasattr(sev, "value"):
        sev_s = str(getattr(sev, "value", "low") or "low")
    else:
        sev_s = str(sev or "low")
    title = str(getattr(finding, "title", "") or "")
    detail = str(getattr(finding, "detail", "") or "")
    if len(detail) > 2000:
        detail = detail[:1997] + "…"
    category = str(getattr(finding, "category", "") or "")
    raw_ev = getattr(finding, "event_indices", None) or []
    event_indices = [int(x) for x in raw_ev if isinstance(x, (int, float, str))]
    raw_up = getattr(finding, "update_indices", None) or []
    update_indices = [int(x) for x in raw_up if isinstance(x, (int, float, str))]
    turn_indices = _finding_turn_indices(segs, event_indices)
    primary_event = event_indices[0] if event_indices else None
    primary_turn = turn_indices[0] if turn_indices else None
    extras_raw = getattr(finding, "extras", None) or {}
    extras: JsonObject = {}
    if isinstance(extras_raw, dict):
        # Keep a short set of MF-style keys for Issue-box paste in HUD.
        for key in (
            "what_model_did",
            "what_should_have_happened",
            "where",
            "why",
            "pattern",
        ):
            if key in extras_raw and extras_raw[key] not in (None, ""):
                val = str(extras_raw[key])
                extras[key] = val[:1200] + ("…" if len(val) > 1200 else "")
    return {
        "id": fid,
        "pluginId": plug,
        "severity": sev_s,
        "title": title,
        "detail": detail,
        "category": category,
        "eventIndices": list(event_indices[:40]),
        "updateIndices": list(update_indices[:40]),
        "turnIndices": list(turn_indices),
        "primaryEventIndex": primary_event,
        "primaryTurnIndex": primary_turn,
        "extras": extras,
    }


def build_session_findings(
    session_dir: Path,
    *,
    segs: list[TurnSegment] | None = None,
    limit: int = DEFAULT_FINDINGS_LIMIT,
) -> JsonObject:
    """Load cached analysis findings and attach turn/event references.

    Reads ``~/.groket/cache/analysis/<session_id>/*.json`` (same layout as the
    TUI analysis cache). Does not re-run analyzers. Stale/mismatched plugin
    versions are still served so palette clients can show last known findings.
    """
    from ..analysis.base import AnalysisResult, Finding
    from ..paths import analysis_cache_dir

    sd = Path(session_dir)
    sid = (sd.name or "").strip()
    cap = max(0, min(int(limit), 200))
    if segs is None:
        segs = segment_timeline_turns(parse_timeline(sd))

    cache_dir = analysis_cache_dir() / "analysis" / sid
    collected: list[JsonObject] = []
    plugins: list[str] = []
    if cache_dir.is_dir():
        # Stable order: plugin file name, then finding order within the file.
        for path in sorted(cache_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                logger.debug("skip findings cache %s", path, exc_info=True)
                continue
            if not isinstance(raw, dict):
                continue
            result_raw = raw.get("result")
            if not isinstance(result_raw, dict):
                if "findings" in raw or "analyzer_id" in raw:
                    result_raw = raw
                else:
                    continue
            try:
                result = AnalysisResult.from_dict(as_json_object(result_raw))
            except (TypeError, ValueError, KeyError):
                logger.debug("skip findings parse %s", path, exc_info=True)
                continue
            plug = (result.analyzer_id or path.stem or "").strip()
            if plug and plug not in plugins:
                plugins.append(plug)
            findings: list[Finding] = list(result.findings or [])
            for f in findings:
                collected.append(finding_mapping(f, segs=segs, plugin_id=plug))

    rows: list[JsonValue] = list(collected[:cap])
    plugins_out: list[JsonValue] = list(plugins)
    return {
        "sessionId": sid,
        "total": len(collected),
        "count": len(rows),
        "truncated": len(collected) > len(rows),
        "plugins": plugins_out,
        "findings": rows,
    }


def build_session_overview(
    session_dir: Path,
    *,
    work_dir: Path | None = None,
    timeline_limit: int = 0,
    content_chars: int = 1500,
) -> JsonObject:
    """Meta + turns + notes + findings for palette clients (timeline lazy).

    Parses the timeline once for turn segmentation and ``numEvents``. Does
    **not** embed event rows — clients call ``session/timeline`` with
    offset/limit (and optional type filter) so large sessions stay cheap.
    *timeline_limit* / *content_chars* are accepted for API compatibility and
    ignored for event embedding.
    """
    _ = (timeline_limit, content_chars)
    sd = Path(session_dir)
    origin = _session_origin(sd, work_dir)
    meta = load_session_meta(sd, include_timeline_count=False)
    meta.origin = origin
    events = parse_timeline(sd)
    meta.num_events = len(events)
    segs = segment_timeline_turns(events)
    notes_rev = ""
    notes_count = 0
    notes_rows: list[JsonValue] = []
    try:
        snap = notes_snapshot(sd)
        notes_rev = snap.revision
        notes_count = len(snap.doc.notes)
        for note in snap.doc.sorted_notes()[:40]:
            notes_rows.append(
                {
                    "id": note.id,
                    "turnIndex": note.turn_index,
                    "fields": dict(note.fields),
                    "eventIndices": list(note.event_indices),
                    "createdAt": note.created_at,
                    "updatedAt": note.updated_at,
                }
            )
    except Exception:
        logger.debug("notes for session/overview %s", sd, exc_info=True)

    findings_block = build_session_findings(sd, segs=segs)

    summary = (meta.summary_text or "").strip()
    if len(summary) > 1200:
        summary = summary[:1197] + "…"

    return {
        "sessionId": (meta.session_id or sd.name).strip(),
        "meta": session_meta_mapping(meta, path=sd, origin=origin),
        "summary": summary,
        "turns": {
            "total": len(segs),
            "turns": [turn_segment_mapping(s, include_event_indexes=True) for s in segs],
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
        },
        "findings": findings_block,
    }


def build_session_timeline(
    session_dir: Path,
    *,
    offset: int = 0,
    limit: int | None = None,
    event_type: str = "",
    prompt_index: int | None = None,
    content_chars: int = DEFAULT_CONTENT_CHARS,
) -> JsonObject:
    """Paged timeline for ``session/timeline``."""
    events = parse_timeline(Path(session_dir))
    # Sequential operator turn ids for HUD/TUI orientation while scrolling.
    turn_by_index = event_display_turn_map(segment_timeline_turns(events))
    type_filter = (event_type or "").strip().casefold()
    filtered: list[TraceEvent] = []
    for ev in events:
        if type_filter and type_filter not in (ev.event_type or "").casefold():
            if type_filter not in (ev.type_label or "").casefold():
                continue
        if prompt_index is not None and ev.prompt_index != prompt_index:
            continue
        filtered.append(ev)
    total = len(filtered)
    off = max(0, int(offset))
    lim = DEFAULT_TIMELINE_LIMIT if limit is None else max(0, min(int(limit), MAX_TIMELINE_LIMIT))
    page = filtered[off : off + lim]
    return {
        "sessionId": Path(session_dir).name,
        "total": total,
        "offset": off,
        "limit": lim,
        "events": [
            timeline_event_mapping(
                ev,
                content_chars=content_chars,
                turn_index=turn_by_index.get(int(ev.index)),
            )
            for ev in page
        ],
    }


def build_session_turns(session_dir: Path) -> JsonObject:
    """Turn segments for ``session/turns``."""
    events = parse_timeline(Path(session_dir))
    segs = segment_timeline_turns(events)
    return {
        "sessionId": Path(session_dir).name,
        "total": len(segs),
        "turns": [turn_segment_mapping(s) for s in segs],
    }


def build_session_usage(session_dir: Path) -> JsonObject:
    """Usage summary for ``session/usage``."""
    events = parse_timeline(Path(session_dir))
    usage = collect_session_usage(Path(session_dir), events)
    out = usage_stats_mapping(usage)
    out["sessionId"] = Path(session_dir).name
    return out


__all__ = [
    "DEFAULT_CONTENT_CHARS",
    "DEFAULT_FINDINGS_LIMIT",
    "DEFAULT_TIMELINE_LIMIT",
    "MAX_CONTENT_CHARS",
    "MAX_TIMELINE_LIMIT",
    "build_session_findings",
    "build_session_get",
    "build_session_overview",
    "build_session_timeline",
    "build_session_turns",
    "build_session_usage",
    "finding_mapping",
    "session_meta_mapping",
    "timeline_event_mapping",
    "turn_segment_mapping",
    "usage_stats_mapping",
]
