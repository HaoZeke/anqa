"""Session Summary — structured UI chrome + Markdown for agent prose."""

from __future__ import annotations

import logging
from collections import Counter
from contextlib import suppress

from rich.console import RenderableType
from rich.text import Text

from ..models import SessionMeta, TraceEvent, json_as_str
from ..parser import extract_prompt
from ..runs.live_share import get_share_display
from ..session.usage_stats import SessionUsageStats
from ..utils import fmt_duration
from .i18n import t
from .panel_render import (
    bullet,
    content_block,
    dim_rule,
    kv_line,
    list_row,
    md_content,
    meta_strip,
    panel_group,
    section_header,
    status_chip,
)

logger = logging.getLogger(__name__)


def _outcome_kind(outcome: str) -> str:
    oc = (outcome or "").lower()
    if oc in ("success", "ok", "completed", "complete"):
        return "ok"
    if oc in ("error", "failed", "failure", "cancelled", "canceled", "timeout"):
        return "bad"
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
    tool_calls = [e for e in timeline if e.event_type == "tool_call"]
    tool_errs = sum(1 for e in tool_calls if e.is_error)
    sess_errs = sum(
        1
        for e in timeline
        if e.event_type == "session_error" or (e.event_type == "session" and e.is_error)
    )
    type_counts = Counter(e.event_type for e in timeline)
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
        outcome = pending
        kind = "unknown"
    blocks: list = []
    head = Text()
    head.append(title + "\n", style="bold")
    head.append("\n  ")
    head.append_text(status_chip(outcome, kind=kind))
    head.append("\n")
    turns = []
    try:
        from ..session.turns import segment_timeline_turns

        turns = segment_timeline_turns(timeline)
    except Exception:
        logger.debug(t("ui-turn-segmentation-failed"), exc_info=True)
        turns = []
    strip_parts = [
        model,
        dur,
        f"{len(timeline)} {t('ui-events-1')}",
        f"{len(tool_calls)} {t('ui-tools-1')}",
    ]
    if len(turns) > 1:
        strip_parts.append(f"{len(turns)} {t('ui-turns')}")
    if tool_errs or sess_errs:
        strip_parts.append(f"{tool_errs} {t('ui-tool-errors')} {sess_errs} {t('ui-session-errors')}")
    head.append_text(meta_strip(strip_parts))
    blocks.append(head)
    blocks.append(dim_rule())
    meta_t = Text()
    meta_t.append_text(kv_line(t("ui-session-2"), meta.session_id or "—"))
    if meta.session_dir:
        path_s = str(meta.session_dir)
        if len(path_s) > 72:
            path_s = "…" + path_s[-69:]
        meta_t.append_text(kv_line(t("ui-path"), path_s))
    if turns:
        meta_t.append_text(kv_line(t("ui-turns-1"), str(len(turns))))
        last = turns[-1]
        meta_t.append_text(
            kv_line(
                t("ui-last-turn"),
                last.label
                + (
                    f" · #{last.first_index}–#{last.last_index}"
                    if last.first_index is not None
                    else ""
                ),
            )
        )
    if meta.loop_count:
        meta_t.append_text(kv_line(t("ui-loops"), str(meta.loop_count)))
    if meta.run_id:
        meta_t.append_text(kv_line(t("ui-run-1"), meta.run_id))
    if meta.task_id:
        meta_t.append_text(kv_line(t("ui-task"), meta.task_id))
    if meta.git_repo:
        meta_t.append_text(kv_line(t("ui-repo"), meta.git_repo))
    if meta.git_branch:
        meta_t.append_text(kv_line(t("ui-branch-1"), meta.git_branch))
    if meta.created_at:
        created = meta.created_at
        if "T" in created and len(created) > 19:
            created = created[:19].replace("T", " ")
        meta_t.append_text(kv_line(t("ui-created"), created))
    if meta.num_messages:
        meta_t.append_text(kv_line(t("ui-messages"), str(meta.num_messages)))
    try:
        info = get_share_display(meta.session_dir)
        url = json_as_str(info.get("share_url")).strip()
        if url:
            meta_t.append_text(kv_line(t("ui-share"), url))
        elif info.get("pending"):
            meta_t.append_text(kv_line(t("ui-share"), t("ui-pending-refresh-with-f5")))
        elif info.get("error"):
            meta_t.append_text(kv_line(t("ui-share"), "failed"))
    except Exception:
        logger.debug(t("ui-share-meta-failed"), exc_info=True)
    blocks.append(meta_t)
    if turns:
        turns_t = Text()
        turns_t.append_text(section_header(t("ui-turns-1")))
        if len(turns) == 1 and (
            not any(
                e.event_type == "session" and "turn" in (e.content or "").lower() for e in timeline
            )
        ):
            turns_t.append(t("ui-single-segment-no-turn-started-markers-in-timeli"), style="dim")
        for seg in turns:
            tools_n = seg.tool_call_count
            terr = seg.tool_error_count
            span = ""
            if seg.first_index is not None and seg.last_index is not None:
                span = f"  #{seg.first_index}–#{seg.last_index}"
            line = (
                f"{seg.label:<22} {t('ui-events-3')} {seg.event_count:<4} {t('ui-tools-2')} {tools_n}"
                + (f" ({terr} {t('ui-err')}" if terr else "")
                + f"{t('ui-user-1')} {seg.user_count} {t('ui-asst-1')} {seg.assistant_count} {span}"
            )
            turns_t.append_text(bullet(line))
            if len(turns) > 1 and tools_n:
                mix_t = Counter(e.tool_name for e in seg.tool_calls if e.tool_name)
                top = ", ".join((f"{n}×{c}" for n, c in mix_t.most_common(4)))
                turns_t.append(f"{t('ui-tools-3')} {top}\n", style="dim")
        blocks.append(turns_t)
    if meta.turn_failed or kind == "bad":
        note = Text()
        note.append_text(section_header(t("ui-note-1")))
        note.append(
            f"{t('ui-last-turn-outcome')} {outcome!r} {t('ui-session-meta-is-last-turn-ended-gate-earlier-tur')}",
            style="dim",
        )
        blocks.append(note)
    mix = Text()
    mix.append_text(section_header(t("ui-event-mix-session")))
    if type_counts:
        for et, c in type_counts.most_common():
            mix.append_text(bullet(f"{et:<16} {c}"))
    else:
        mix.append(t("ui-none"), style="dim")
    blocks.append(mix)
    try:
        from ..session.usage_stats import collect_session_usage

        usage = collect_session_usage(meta.session_dir, timeline)
        usage_t = Text()
        append_usage_rich(usage_t, usage)
        if usage_t.plain.strip():
            blocks.append(usage_t)
    except Exception:
        logger.debug(t("ui-usage-summary-failed"), exc_info=True)
        tool_mix = Counter(e.tool_name for e in tool_calls if e.tool_name)
        if tool_mix:
            tools_t = Text()
            tools_t.append_text(section_header(t("ui-tools")))
            for n, c in tool_mix.most_common():
                tools_t.append_text(bullet(f"{n:<24} {c}×"))
            blocks.append(tools_t)
    with suppress(Exception):
        share_t = Text()
        info = get_share_display(meta.session_dir)
        share_t.append_text(section_header(t("ui-share")))
        url = json_as_str(info.get("share_url")).strip()
        if url:
            share_t.append_text(kv_line(t("ui-url"), url))
        elif info.get("pending"):
            share_t.append_text(kv_line(t("ui-url"), "pending"))
        else:
            share_t.append_text(kv_line(t("ui-url"), "—"))
        if info.get("snapshot_n"):
            share_t.append_text(kv_line(t("ui-snapshot"), f"#{info['snapshot_n']}"))
        blocks.append(share_t)
    prompt = extract_prompt(meta.session_dir)
    if prompt:
        blocks.append(section_header(t("ui-prompt-1")))
        blocks.append(content_block(prompt, max_chars=8000))
    if assistant_text.strip():
        blocks.append(section_header(t("ui-assistant")))
        blocks.append(md_content(assistant_text.strip(), max_chars=60000))
    foot = Text()
    foot.append_text(section_header(t("ui-workspace")))
    foot.append(t("ui-open-the-diff-tab-for-rewind-and-search-replace"), style="dim")
    blocks.append(foot)
    return panel_group(*blocks)


def append_usage_rich(out: Text, usage: SessionUsageStats) -> None:
    """Append host tools / MCP / skills onto a Rich Text (Summary tab).

    :param out: Rich :class:`~rich.text.Text` instance to append into.
    :param usage: Collected session usage statistics.
    """
    if usage.persona_id:
        out.append_text(kv_line(t("ui-persona-2"), usage.persona_id))
    out.append_text(section_header(t("ui-host-tools")))
    if usage.host_tools:
        for row in usage.host_tools:
            err = f"  ({row.errors} {t('ui-errors-3')}" if row.errors else ""
            out.append_text(list_row(f"{row.name:<24} {row.calls}×{err}"))
        out.append_text(list_row(f"{'total':<24} {usage.host_tool_call_total}"))
    else:
        out.append(t("ui-none"), style="dim")
    if usage.mcp_bridge_calls:
        out.append_text(list_row(f"{t('ui-mcp-bridge-calls'):<24} {usage.mcp_bridge_calls}"))
    out.append_text(section_header(t("ui-mcp")))
    if not usage.mcp_servers and (not usage.mcp_configured):
        out.append(t("ui-none"), style="dim")
    else:
        for srv in usage.mcp_servers:
            cfg = "configured" if srv.configured else t("ui-from-calls")
            out.append_text(list_row(f"{srv.server_id}  ({cfg})"))
            if not srv.methods and (not srv.search_queries) and (not srv.use_tool_calls):
                out.append(t("ui-enabled-no-tool-hits"), style="dim")
                continue
            if srv.use_tool_calls:
                out.append(f"{t('ui-use-tool')} {srv.use_tool_calls}×\n", style="dim")
            for m in srv.methods:
                out.append(f"    .{m.method}  {m.calls}×\n", style="dim")
    out.append_text(section_header(t("ui-skills-1")))
    if not usage.skills and (not usage.skills_configured):
        out.append(t("ui-none"), style="dim")
    else:
        for sk in usage.skills:
            bits = []
            if sk.configured:
                bits.append("mounted")
            if sk.skill_md_reads:
                bits.append(f"{t('ui-loaded-1')} {sk.skill_md_reads}×")
            out.append_text(list_row(f"{sk.skill_id}  — {', '.join(bits) or 'seen'}"))
    if usage.source_notes:
        out.append(f"{t('ui-sources')} {', '.join(usage.source_notes)}\n", style="dim")


def assistant_text_from_timeline(timeline: list[TraceEvent]) -> str:
    return "".join(e.content for e in timeline if e.event_type == "assistant" and e.content)
