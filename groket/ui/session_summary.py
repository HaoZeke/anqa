"""Session Summary — structured UI chrome + Markdown for agent prose."""

from __future__ import annotations

import logging

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from .. import event_types as et
from ..models import SessionMeta, TraceEvent, json_as_str
from ..runs.live_share import get_share_display
from ..session.subagents import is_subagent_session_dir
from ..session.usage_stats import SessionUsageStats
from ..utils import fmt_duration
from .i18n import join_ui, t
from .panel_render import (
    dim_rule,
    kv_line,
    list_row,
    meta_strip,
    panel_group,
    section_header,
    status_chip,
)

logger = logging.getLogger(__name__)


def _outcome_kind(outcome: str) -> str:
    oc = (outcome or "").lower().replace(" ", "_")
    if oc in ("success", "ok", "completed", "complete"):
        return "ok"
    if oc in ("error", "failed", "failure", "cancelled", "canceled", "timeout"):
        return "bad"
    if oc in ("ending", "finishing") or oc.startswith("ending_"):
        return "ending"
    if oc in ("running", "in_progress", "pending") or oc.startswith("agent_running"):
        return "running"
    if oc.startswith("awaiting"):
        return "awaiting"
    return "unknown"


def build_session_summary(
    meta: SessionMeta, timeline: list[TraceEvent], *, assistant_text: str = ""
) -> str:
    try:
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        Console(file=buf, force_terminal=False, width=100).print(
            render_session_summary(meta, timeline, assistant_text=assistant_text)
        )
        return buf.getvalue()
    except Exception:
        return str(meta.title or meta.session_id or "")


def render_session_summary(
    meta: SessionMeta, timeline: list[TraceEvent], *, assistant_text: str = ""
) -> RenderableType:
    _ = assistant_text
    tool_calls = [e for e in timeline if e.event_type == "tool_call"]
    tools_n = max(int(meta.tool_call_count or 0), len(tool_calls))
    events_n = max(int(meta.num_events or 0), len(timeline))
    tool_errs = sum(1 for e in tool_calls if e.is_error)
    sess_errs = sum(
        1
        for e in timeline
        if e.event_type in et.ERROR_TYPES or (e.event_type in et.TURN_BOUNDARY_TYPES and e.is_error)
    )
    title = (meta.title or meta.session_id or "session").strip()
    outcome = (meta.turn_outcome or "").strip() or "unknown"
    dur = fmt_duration(meta.duration_seconds) if meta.duration_seconds else "—"
    model = (meta.model_display or "").strip() or "—"
    kind = _outcome_kind(outcome)
    pending = ""
    try:
        from ..session.turn_gate import session_pending_label

        pending = session_pending_label(
            meta.session_dir, turn_in_progress=bool(meta.turn_in_progress)
        )
    except Exception:
        pending = ""
    if pending:
        from .session_status import localize_session_pending_label

        outcome, kind = localize_session_pending_label(pending)
    elif (meta.turn_outcome or "").strip().lower().replace(" ", "_") in (
        "ending",
        "finishing",
    ):
        outcome = t("status-ending")
        kind = "ending"
    blocks: list = []
    head = Text()
    head.append(title + "\n", style="bold")
    head.append("\n  ")
    if meta.session_dir and is_subagent_session_dir(meta.session_dir):
        head.append_text(status_chip(t("ui-subagent"), kind="unknown"))
        head.append("  ")
    head.append_text(status_chip(outcome, kind=kind))
    head.append("\n")
    turns = []
    try:
        from ..session.turns import segment_timeline_turns

        turns = segment_timeline_turns(timeline)
    except Exception:
        logger.debug(t("ui-turn-segmentation-failed"), exc_info=True)
        turns = []
    turns_n = max(int(meta.turn_count or 0), len(turns))
    strip_parts = [
        model,
        dur,
        t("count-events", n=events_n),
        t("count-tools", n=tools_n),
    ]
    if meta.has_context_usage:
        strip_parts.append(meta.context_usage_compact or meta.context_usage_str)
    if turns_n > 1:
        strip_parts.append(t("count-turns", n=turns_n))
    if tool_errs or sess_errs:
        strip_parts.append(f"{tool_errs} tool errors · {sess_errs} session errors")
    head.append_text(meta_strip(strip_parts))
    blocks.append(head)
    blocks.append(dim_rule())
    blocks.append(_glance_columns(meta, turns))
    if meta.turn_failed or kind == "bad":
        note = Text()
        note.append_text(section_header(t("ui-note-1")))
        note.append(
            t("browser-last-turn-outcome-note", outcome=repr(outcome)),
            style="dim",
        )
        blocks.append(note)
    return panel_group(*blocks)


def _glance_columns(meta: SessionMeta, turns: list) -> Table:
    """Two-column glance fields (path / context left, turns / ids right)."""
    left = Text()
    right = Text()
    left.append_text(kv_line(t("ui-session-2"), meta.session_id or "—"))
    if meta.has_context_usage:
        left.append_text(kv_line(t("ui-context-usage"), meta.context_usage_str or "—"))
        if meta.context_tokens_used is not None and meta.context_window_tokens is not None:
            left.append_text(
                kv_line(
                    t("ui-context-tokens"),
                    f"{meta.context_tokens_used:,} / {meta.context_window_tokens:,}",
                )
            )
        elif meta.context_tokens_used is not None:
            left.append_text(kv_line(t("ui-context-tokens"), f"{meta.context_tokens_used:,}"))
        if meta.compaction_count:
            left.append_text(kv_line(t("ui-compactions"), str(meta.compaction_count)))
    if meta.session_dir:
        path_s = str(meta.session_dir)
        if len(path_s) > 48:
            path_s = "…" + path_s[-45:]
        left.append_text(kv_line(t("ui-path"), path_s))
    turns_n = max(int(meta.turn_count or 0), len(turns))
    if turns_n > 1:
        right.append_text(kv_line(t("ui-turns-1"), str(turns_n)))
        if turns:
            last = turns[-1]
            last_label = last.label
            if turns_n > len(turns):
                extra = " (open)" if last.open else (f" ({last.outcome})" if last.outcome else "")
                last_label = f"turn {turns_n - 1}{extra}"
            right.append_text(kv_line(t("ui-last-turn"), last_label))
    if meta.created_at:
        created = meta.created_at
        if "T" in created and len(created) > 19:
            created = created[:19].replace("T", " ")
        right.append_text(kv_line(t("ui-created"), created))
    if meta.git_branch:
        right.append_text(kv_line(t("ui-branch-1"), meta.git_branch))
    if meta.run_id:
        right.append_text(kv_line(t("ui-run-1"), meta.run_id))
    if meta.task_id:
        right.append_text(kv_line(t("ui-task"), meta.task_id))
    try:
        info = get_share_display(meta.session_dir)
        url = json_as_str(info.get("share_url")).strip()
        if url:
            right.append_text(kv_line(t("ui-share"), url))
    except Exception:
        logger.debug(t("ui-share-meta-failed"), exc_info=True)
    grid = Table.grid(expand=True, padding=(0, 2, 0, 0))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(left, right)
    return grid


def append_usage_rich(out: Text, usage: SessionUsageStats) -> None:
    """Append host tools / MCP / skills onto a Rich Text (Summary tab).

    Copy comes from Fluent (``t(...)``); Rich styles are applied around full
    messages, not f-string fragment glue.

    :param out: Rich :class:`~rich.text.Text` instance to append into.
    :param usage: Collected session usage statistics.
    """
    if usage.persona_id:
        out.append_text(kv_line(t("ui-persona-2"), usage.persona_id))
    out.append_text(section_header(t("ui-host-tools")))
    if usage.host_tools:
        for row in usage.host_tools:
            err_suffix = t("ui-host-tool-errors", n=row.errors) if row.errors else ""
            # Pad tool id for alignment only (not user-facing copy).
            label = f"{row.name:<24}"
            out.append_text(
                list_row(join_ui(label, t("ui-host-tool-calls", n=row.calls), err_suffix))
            )
        out.append_text(list_row(t("ui-host-tool-total", n=usage.host_tool_call_total)))
    else:
        out.append(t("ui-none") + "\n", style="dim")
    if usage.mcp_bridge_calls:
        out.append_text(kv_line(t("ui-mcp-bridge-calls"), str(usage.mcp_bridge_calls)))
    out.append_text(section_header(t("ui-mcp")))
    if not usage.mcp_servers and (not usage.mcp_configured):
        out.append(t("ui-none") + "\n", style="dim")
    else:
        # Idle = dim name only; used = magenta name + indented methods.
        # Do not dump search_tool query text (noisy); optional search count only.
        for srv in usage.mcp_servers:
            used = bool(srv.methods or srv.search_queries or srv.use_tool_calls)
            name_style = "bold magenta" if used else "dim"
            mcp_line = Text()
            mcp_line.append("  ")
            mcp_line.append(srv.server_id, style=name_style)
            if srv.errors:
                mcp_line.append(" ")
                mcp_line.append(t("ui-err-count", n=srv.errors), style="red")
            mcp_line.append("\n")
            out.append_text(mcp_line)
            if not used:
                continue
            for m in srv.methods:
                line = Text()
                line.append("    ")
                line.append(
                    t("ui-mcp-method-line", method=m.method, calls=m.calls),
                    style="magenta",
                )
                if m.errors:
                    line.append(" ")
                    line.append(t("ui-err-count-paren", n=m.errors), style="red")
                line.append("\n")
                out.append_text(line)
            n_search = len(srv.search_queries)
            if n_search and not srv.methods:
                # Searched the catalog but never invoked a method on this server.
                out.append(
                    "    " + t("ui-mcp-search-count", n=n_search) + "\n",
                    style="dim",
                )
    out.append_text(section_header(t("ui-skills-1")))
    if not usage.skills and (not usage.skills_configured):
        out.append(t("ui-none") + "\n", style="dim")
    else:
        for sk in usage.skills:
            bits: list[str] = []
            if sk.configured:
                bits.append(t("browser-skill-mounted"))
            if sk.skill_md_reads:
                bits.append(t("ui-skill-loaded", n=sk.skill_md_reads))
            out.append_text(
                list_row(
                    t(
                        "ui-skill-line",
                        id=sk.skill_id,
                        bits=", ".join(bits) or t("browser-skill-seen"),
                    )
                )
            )
    if usage.source_notes:
        out.append(
            t("ui-sources-line", notes=", ".join(usage.source_notes)) + "\n",
            style="dim",
        )


def assistant_text_from_timeline(timeline: list[TraceEvent]) -> str:
    return "".join(e.content for e in timeline if e.event_type in et.AGENT_TYPES and e.content)
