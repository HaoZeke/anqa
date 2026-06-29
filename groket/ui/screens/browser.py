"""Trace browser screen — interactive timeline with detail view and feedback."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult

from ..data_table import style_data_table
from ..i18n import t

logger = logging.getLogger(__name__)
from collections import Counter, defaultdict

from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from ...analysis import get_analysis_service
from ...analysis.base import AnalysisResult, Finding
from ...constants import DIFF_TRUNCATE_HEAD, DIFF_TRUNCATE_TAIL, DIFF_TRUNCATE_THRESHOLD
from ...flags import load_flags, save_flags
from ...models import Flag, SessionMeta, TraceEvent
from ...parser import load_session_meta, parse_timeline
from ...paths import APP_HOME
from ...session.workspace_diff import format_diff_meta_line, load_workspace_diff
from ...utils import fmt_duration
from .. import text as U
from ..bindings import BROWSER, ChromeActions, focus_primary_list
from ..panel_render import (
    TipSurface,
    bullet,
    content_block,
    dim_rule,
    kv_line,
    meta_strip,
    panel_group,
    refresh_all_tip_surfaces,
    section_header,
    status_chip,
)
from ..session_summary import assistant_text_from_timeline, render_session_summary
from ..styles import SEVERITY_LABEL
from ..widgets.controls import FILTER_BAR_CLASS, FILTER_LABEL_CLASS
from ..widgets.detail_view import DetailView
from ..widgets.flag_panel import FlagModal
from ..widgets.timeline import TimelineTable

_BROWSER_TABS: tuple[tuple[str, str], ...] = (
    ("tab-timeline", "#timeline-list"),
    ("tab-findings", "#findings-table"),
    ("tab-summary", "#summary-scroll"),
    ("tab-diff", "#diff-scroll"),
    ("tab-reports", "#reports-scroll"),
    ("tab-stats", "#stats-scroll"),
)


class BrowserScreen(ChromeActions):
    """Interactive trace browser with timeline, detail view, and findings."""

    BINDINGS = list(BROWSER)

    def __init__(
        self, session_dir: Path, plugin_results: dict[str, AnalysisResult] | None = None, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.session_dir = session_dir
        self.meta: SessionMeta | None = None
        self.timeline: list[TraceEvent] = []
        self.plugin_results: dict[str, AnalysisResult] = plugin_results or {}
        self._findings: list[Finding] = []
        self._findings_by_call: dict[str, Finding] = {}
        self._errors_only = False
        self._current_event: TraceEvent | None = None
        self._findings_table_entries: list[Finding] = []
        self._selected_finding: Finding | None = None
        self._flags: dict[int, Flag] = {}
        self._load_started = False
        self._diff_md: str = ""
        self._diff_meta: dict = {}
        self._timeline_filter: str = "all"
        self._report_section_keys: set[str] = set()
        self._report_filter: str = "all"
        self._report_select_options_key: tuple[str, ...] = ()
        self._report_updating: bool = False
        self._live_refresh_timer: Timer | None = None
        self._live_refresh_busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        from ..widgets.activity_bar import ActivityBar

        yield ActivityBar()
        with Vertical(id="session-pending-bar"):
            yield Static("", id="session-pending-status")
            yield Static("", id="session-pending-queue")
            yield Input(placeholder=U.follow_up_placeholder_send(), id="session-follow-input")
            with Horizontal(id="session-pending-actions"):
                yield Button(
                    U.follow_up_btn_send(), id="session-follow-send-btn", variant="primary"
                )
                yield Button(U.follow_up_btn_done(), id="session-follow-done-btn")
        with TabbedContent(id="browser-tabs"):
            with TabPane(U.tab_timeline(), id="tab-timeline"):
                with Horizontal(id="browser-layout"):
                    with Vertical(id="timeline-panel"):
                        with Horizontal(id="filter-bar", classes=FILTER_BAR_CLASS):
                            yield Static(
                                t("ui-filter"), id="filter-view-label", classes=FILTER_LABEL_CLASS
                            )
                            yield Select(
                                [
                                    (U.all_events(), "all"),
                                    (U.tools_only(), "tools"),
                                    (U.user_messages(), "user"),
                                    (U.assistant_messages(), "asst"),
                                    (U.session_markers(), "sess"),
                                    (U.errors_only(), "errors"),
                                ],
                                value="all",
                                id="timeline-view-select",
                                allow_blank=False,
                                classes="field-select",
                            )
                            turn_sel = Select(
                                [(t("turn-filter-all"), "all")],
                                value="all",
                                id="timeline-turn-select",
                                allow_blank=False,
                                classes="field-select",
                            )
                            turn_sel.display = False  # shown only when multi-turn
                            yield turn_sel
                            yield Input(
                                placeholder=U.search_events_placeholder(), id="search-input"
                            )
                        yield TimelineTable(id="timeline-list")
                    yield DetailView(id="detail-panel")
            with TabPane(U.tab_findings(), id="tab-findings"):
                with Vertical(id="findings-panel"):
                    with Vertical(classes="panel-card"):
                        yield Static("", id="findings-header")
                        yield TipSurface(U.tip_findings_row(), id="findings-tip")
                    with Vertical(classes=t("ui-panel-card-panel-card-grow")):
                        yield DataTable(id="findings-table")
            with TabPane(U.tab_summary(), id="tab-summary"):
                with VerticalScroll(id="summary-scroll"):
                    with Vertical(classes="panel-card"):
                        yield Static(id="summary-content")
                        yield TipSurface(U.tip_share_url(), id="summary-share-tip")
            with TabPane(U.tab_diff(), id="tab-diff"):
                with VerticalScroll(id="diff-scroll"):
                    with Vertical(classes="panel-card"):
                        yield Static(id="diff-content")
            with TabPane(U.tab_report(), id="tab-reports"):
                with Vertical(id="reports-panel"):
                    with Horizontal(id="report-filter-bar", classes=FILTER_BAR_CLASS):
                        yield Static(U.filter_label(), classes=FILTER_LABEL_CLASS)
                        yield Select(
                            [(U.all_sections(), "all"), (U.flags_only(), "flags")],
                            value="all",
                            id="report-view-select",
                            allow_blank=False,
                            classes=t("ui-field-select-report-view-select"),
                        )
                    with VerticalScroll(id="reports-scroll"):
                        with Vertical(classes="panel-card", id="report-section-overview"):
                            yield Static(id="report-overview-content")
                            yield TipSurface(U.tip_report_filter(), id="report-overview-tip")
                            yield TipSurface("", id="report-analysis-tip")
                        with Vertical(
                            classes=t("ui-panel-card-report-section"), id="report-section-flags"
                        ):
                            yield Static(id="report-flags-content")
                            yield TipSurface(U.tip_no_flags(), id="report-flags-tip")
                        yield Vertical(id="report-sections-host")
            with TabPane(U.tab_stats(), id="tab-stats"):
                with VerticalScroll(id="stats-scroll"):
                    with Vertical(classes="panel-card"):
                        yield Static(id="stats-header")
                    with Vertical(classes="panel-card"):
                        yield Static(t("ui-turns-1"), classes="panel-card-title")
                        yield DataTable(id="stats-turns-table")
                    with Vertical(classes="panel-card"):
                        yield Static(U.event_types(), classes="panel-card-title")
                        yield DataTable(id="stats-events-table")
                    with Vertical(classes="panel-card"):
                        yield Static(U.tool_timing(), classes="panel-card-title")
                        yield DataTable(id="stats-tools-table")
                    with Vertical(classes="panel-card"):
                        yield Static(U.time_breakdown(), classes="panel-card-title")
                        yield DataTable(id="stats-phases-table")
                    with Vertical(classes="panel-card", id="stats-usage-card"):
                        yield Static(id="stats-usage")
        yield Footer()

    def on_mount(self) -> None:
        if self._load_started:
            return
        self._load_started = True
        try:
            style_data_table(self.query_one("#findings-table", DataTable))
            for tid in (
                "#stats-turns-table",
                "#stats-events-table",
                "#stats-tools-table",
                "#stats-phases-table",
            ):
                style_data_table(self.query_one(tid, DataTable))
        except Exception:
            pass
        self._load_data()

    def on_unmount(self) -> None:
        self._stop_live_refresh()

    def _stop_live_refresh(self) -> None:
        t = self._live_refresh_timer
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass
        self._live_refresh_timer = None

    def _session_is_pending(self) -> bool:
        """True while agent may still run or interactive gate awaits a follow-up."""
        from ...session.turn_gate import session_awaits_follow_up, session_pending_label

        meta = self.meta
        if meta and meta.turn_in_progress:
            return True
        if meta and (not (meta.turn_outcome or "").strip()):
            return True
        try:
            if session_awaits_follow_up(self.session_dir):
                return True
            if session_pending_label(
                self.session_dir, turn_in_progress=bool(meta and meta.turn_in_progress)
            ):
                return True
        except Exception:
            pass
        return False

    def _refresh_session_pending_bar(self) -> None:
        from ...session.turn_gate import (
            drain_queued_follow_up,
            list_queued_follow_ups,
            read_turn_gate_status,
            session_pending_label,
        )

        try:
            drained = drain_queued_follow_up(self.session_dir)
            if drained:
                preview = drained if len(drained) <= 48 else drained[:48] + "…"
                self.notify(f"{t('ui-queued-follow-up-sent')}{preview})")
        except Exception:
            pass
        meta = self.meta
        label = ""
        try:
            label = session_pending_label(
                self.session_dir, turn_in_progress=bool(meta and meta.turn_in_progress)
            )
        except Exception:
            label = ""
        if not label and meta and (not (meta.turn_outcome or "").strip()):
            label = t("ui-session-outcome-pending")
        if not label and meta and meta.turn_in_progress:
            label = t("ui-turn-in-progress")
        st = {}
        try:
            st = read_turn_gate_status(self.session_dir)
        except Exception:
            pass
        queued: list[str] = []
        try:
            queued = list_queued_follow_ups(self.session_dir)
        except Exception:
            queued = []
        show = (
            bool(label)
            or bool(queued)
            or str(st.get("state") or "") in ("awaiting_follow_up", "running")
        )
        try:
            bar = self.query_one("#session-pending-bar")
            bar.display = show
        except Exception:
            pass
        try:
            status = self.query_one("#session-pending-status", Static)
            chip = status_chip(label or "idle", kind="unknown" if not label else "ok")
            sid = str(st.get("session_id") or (meta.session_id if meta else ""))
            turn = st.get("turn", "")
            extra = f"{t('ui-session-1')}{sid}" if sid else ""
            if turn != "" and turn is not None:
                extra += t("ui-gate-turn", turn=turn)
            if queued:
                extra += t("ui-queued-count", n=len(queued))
            status.update(Text.assemble(chip, extra))
        except Exception:
            pass
        try:
            q_widget = self.query_one("#session-pending-queue", Static)
            if queued:
                lines = [f"[bold yellow]{len(queued)}{t('ui-follow-up-s-pending')}"]
                for i, p in enumerate(queued[:5], start=1):
                    preview = p.replace("\n", " ")
                    if len(preview) > 72:
                        preview = preview[:69] + "…"
                    lines.append(f"  {i}. {preview}")
                if len(queued) > 5:
                    lines.append(f"  … +{len(queued) - 5}{t('ui-more-1')}")
                q_widget.update("\n".join(lines))
                q_widget.display = True
            else:
                q_widget.update("")
                q_widget.display = False
        except Exception:
            pass
        awaiting = str(st.get("state") or "") == "awaiting_follow_up"
        try:
            self.query_one("#session-follow-send-btn", Button).disabled = not show
            self.query_one("#session-follow-done-btn", Button).disabled = not show
        except Exception:
            pass
        try:
            hint = self.query_one("#session-follow-input", Input)
            if awaiting:
                hint.placeholder = U.follow_up_placeholder_awaiting()
            elif show:
                hint.placeholder = U.follow_up_placeholder_queue()
            hint.disabled = not show
        except Exception:
            pass
        try:
            self.refresh_bindings()
        except Exception:
            pass

    def _session_follow_send(self) -> None:
        from ...session.turn_gate import write_follow_up_for_session

        try:
            text = self.query_one("#session-follow-input", Input).value.strip()
        except Exception:
            text = ""
        if not text:
            self.notify(U.follow_up_empty(), severity="warning")
            return
        try:
            how = write_follow_up_for_session(self.session_dir, text)
            self.query_one("#session-follow-input", Input).value = ""
            if how == "queued":
                self.notify(U.follow_up_queued())
            else:
                self.notify(U.follow_up_sent())
            rm = getattr(self.app, "run_manager", None)
            if rm is not None and hasattr(rm, "submit_follow_up") and (how == "sent"):
                try:
                    rid = (self.meta.run_id if self.meta else "") or ""
                    rm.submit_follow_up(text, run_id=rid)
                except Exception:
                    pass
        except Exception as exc:
            self.notify(U.follow_up_failed(exc), severity="error")
        self._refresh_session_pending_bar()
        self._schedule_live_refresh()

    def _session_follow_done(self) -> None:
        from ...session.turn_gate import write_done_for_session

        try:
            write_done_for_session(self.session_dir)
            rm = getattr(self.app, "run_manager", None)
            if rm is not None and hasattr(rm, "complete_interactive"):
                rid = (self.meta.run_id if self.meta else "") or ""
                try:
                    rm.complete_interactive(rid)
                except Exception:
                    pass
            self.notify(U.mark_session_done_ok())
        except Exception as exc:
            self.notify(U.mark_session_done_failed(exc), severity="error")
        self._refresh_session_pending_bar()
        self._schedule_live_refresh()

    @on(Button.Pressed, "#session-follow-send-btn")
    def _on_session_follow_send_btn(self) -> None:
        self._session_follow_send()

    @on(Button.Pressed, "#session-follow-done-btn")
    def _on_session_follow_done_btn(self) -> None:
        self._session_follow_done()

    @on(Input.Submitted, "#session-follow-input")
    def _on_session_follow_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._session_follow_send()

    def action_send_follow_up(self) -> None:
        """Send or queue follow-up from the pending bar (when interactive)."""
        if not self.check_action("send_follow_up", ()):
            return
        self._session_follow_send()

    def action_mark_session_done(self) -> None:
        """``e`` — end interactive session when awaiting / multi-turn."""
        if not self.check_action("mark_session_done", ()):
            return
        self._session_follow_done()

    def action_focus_follow_up(self) -> None:
        """``n`` — focus the next-prompt field when the session supports it."""
        if not self.check_action("focus_follow_up", ()):
            return
        try:
            inp = self.query_one("#session-follow-input", Input)
            if not inp.display and (not self._pending_bar_visible()):
                return
            inp.focus()
        except Exception:
            pass

    def _pending_bar_visible(self) -> bool:
        try:
            return bool(self.query_one("#session-pending-bar").display)
        except Exception:
            return False

    def _schedule_live_refresh(self) -> None:
        """While session is pending (turn or interactive gate), reload timeline."""
        self._stop_live_refresh()
        if not self._session_is_pending():
            self._refresh_session_pending_bar()
            return
        try:
            from ...constants import LIVE_REFRESH_INTERVAL

            self._live_refresh_timer = self.set_timer(
                LIVE_REFRESH_INTERVAL, self._live_refresh_tick
            )
        except Exception:
            pass
        self._refresh_session_pending_bar()

    def _live_refresh_tick(self) -> None:
        if self._live_refresh_busy:
            self._schedule_live_refresh()
            return
        self._live_refresh_busy = True
        try:
            self._load_data_light()
        except Exception:
            pass
        finally:
            self._live_refresh_busy = False
            self._schedule_live_refresh()

    @work(thread=True)
    def _load_data_light(self) -> None:
        """Reload meta + timeline only (no re-run detectors/diff — for live monitor)."""
        self.meta = load_session_meta(self.session_dir)
        self.timeline = parse_timeline(self.session_dir)
        self._rebuild_indices()
        self.app.call_from_thread(self._populate_ui_light)

    def _populate_ui_light(self) -> None:
        """Update title + timeline + share/stats without rebuilding analysis tabs."""
        self._set_title_from_meta()
        try:
            timeline_table = self.query_one("#timeline-list", TimelineTable)
            timeline_table.load_events(self.timeline, self._findings, list(self._flags.values()))
            self._rebuild_turn_select()
        except Exception:
            pass
        try:
            self._update_stats()
        except Exception:
            pass
        self._refresh_session_pending_bar()

    @work(thread=True)
    def _load_data(self) -> None:
        self.meta = load_session_meta(self.session_dir)
        self.timeline = parse_timeline(self.session_dir)
        self._load_flags()
        self._rebuild_indices()
        try:
            self._diff_md, self._diff_meta = load_workspace_diff(self.session_dir)
        except Exception:
            self._diff_md = "# Workspace diff\n\n_Failed to load diff._\n"
            self._diff_meta = {}
        self.app.call_from_thread(self._populate_ui)
        self.app.call_from_thread(self._schedule_live_refresh)
        self._run_analysis()

    def _run_analysis(self) -> None:
        """Run analysis plugins (slow path).

        Called from the _load_data worker thread; pushes UI updates back
        to the main thread when done.
        """
        is_live = bool(self.meta and self.meta.turn_in_progress)
        svc = get_analysis_service()
        auto = bool(getattr(svc.config, "auto_analyze_on_open", True))
        if not self.plugin_results and (not is_live) and auto:
            try:
                self.plugin_results = svc.analyze_all(self.session_dir)
            except Exception:
                pass
        self._collect_findings()
        self._rebuild_indices()
        self.app.call_from_thread(self._populate_analysis_ui)

    def _load_flags(self) -> None:
        """Load user flags from disk into a dict keyed by event_index."""
        self._flags = {fl.event_index: fl for fl in load_flags(self.session_dir)}

    def _enabled_analyzer_ids(self) -> set[str] | None:
        """Ids enabled in the process analysis service (None if unavailable)."""
        try:
            from ...analysis import get_analysis_service

            return set(get_analysis_service().enabled_ids)
        except Exception:
            return None

    def _active_plugin_results(self) -> dict[str, AnalysisResult]:
        """Results for analyzers enabled under the current config only."""
        enabled = self._enabled_analyzer_ids()
        if not enabled:
            return dict(self.plugin_results)
        out: dict[str, AnalysisResult] = {}
        for key, result in self.plugin_results.items():
            aid = key
            if result is not None and getattr(result, "analyzer_id", None):
                aid = result.analyzer_id
            if aid in enabled or key in enabled:
                out[key] = result
        return out

    def _collect_findings(self) -> None:
        """Collect findings from **enabled** plugin results only."""
        all_findings: list[Finding] = []
        for result in self._active_plugin_results().values():
            if result is not None and result.ok:
                all_findings.extend(result.findings)
        self._findings = sorted(all_findings, key=lambda f: f.severity)

    def _rebuild_indices(self) -> None:
        self._findings_by_call = {}
        for finding in self._findings:
            for cid in finding.all_tool_call_ids:
                if cid not in self._findings_by_call:
                    self._findings_by_call[cid] = finding

    def _set_title_from_meta(self) -> None:
        label = self.meta.label if self.meta else self.session_dir.name
        model = self.meta.model_id if self.meta else "unknown"
        outcome_bit = ""
        if self.meta and self.meta.turn_outcome:
            if self.meta.turn_failed:
                outcome_bit = f"{t('ui-turn-1')}{self.meta.turn_outcome}"
            elif self.meta.turn_in_progress:
                outcome_bit = f"{t('ui-live-turn')}{self.meta.turn_outcome}"
            else:
                outcome_bit = f"{t('ui-turn-2')}{self.meta.turn_outcome}"
        elif self.meta:
            outcome_bit = t("ui-live")
        self.title = f"{t('ui-browser')}{label} ({model}){outcome_bit}"

    def _populate_ui(self) -> None:
        """Phase 1 UI: title, timeline, diff, summary, stats — file I/O only."""
        self._set_title_from_meta()
        timeline_table = self.query_one("#timeline-list", TimelineTable)
        timeline_table.load_events(self.timeline, self._findings, list(self._flags.values()))
        self._rebuild_turn_select()
        self._update_diff_tab()
        self._update_summary_tab()
        self._update_stats()
        self._show_analysis_pending()
        self._update_reports_tab()
        timeline_table.focus()
        if self.meta and self.meta.turn_failed:
            self.notify(
                f"{t('ui-turn-ended-with-outcome')}{self.meta.turn_outcome}{t('ui-see-summary-tab-or-session-session-error-timelin')}",
                severity="warning",
                timeout=8,
            )

    def _show_analysis_pending(self) -> None:
        """Show loading placeholders in tabs that depend on analysis."""
        try:
            findings_table = self.query_one("#findings-table", DataTable)
            findings_table.clear(columns=True)
            findings_table.add_columns("", "")
            findings_table.add_row("", t("ui-running-analysis"))
        except Exception:
            pass
        try:
            self.query_one("#report-overview-content", Static).update(t("ui-running-analysis-1"))
        except Exception:
            pass
        for aid in list(getattr(self, "_report_section_keys", ()) or ()):
            if aid == "flags":
                continue
            try:
                self.query_one(f"#{self._report_content_dom_id(aid)}", Static).update(
                    t("ui-running-analysis-1")
                )
            except Exception:
                pass

    def _populate_analysis_ui(self) -> None:
        """Phase 2 UI: findings + reports — after analysis plugins finish."""
        timeline_table = self.query_one("#timeline-list", TimelineTable)
        timeline_table.load_events(self.timeline, self._findings, list(self._flags.values()))
        self._rebuild_turn_select()
        findings_table = self.query_one("#findings-table", DataTable)
        findings_table.clear(columns=True)
        style_data_table(findings_table)
        findings_table.add_columns(
            U.col_severity(), U.col_plugin(), U.col_category(), U.col_title(), U.col_events()
        )
        self._findings_table_entries = []
        try:
            self._update_findings_header()
        except Exception:
            pass
        for row_idx, finding in enumerate(self._findings):
            sev_display = SEVERITY_LABEL.get(finding.severity.value, finding.severity.value)
            n_events = len(finding.all_tool_call_ids)
            title = finding.title[:60]
            if finding.children:
                title = f"{title} (+)"
            findings_table.add_row(
                sev_display,
                finding.plugin_id,
                finding.category,
                title,
                str(n_events),
                key=str(row_idx),
            )
            self._findings_table_entries.append(finding)
        self._update_reports_tab()

    @staticmethod
    def _fmt_dur(seconds: float) -> str:
        return fmt_duration(seconds)

    def refresh_tip_surfaces(self) -> None:
        """Refresh all TipSurface widgets on this screen (class tip-surface only)."""
        refresh_all_tip_surfaces(self)
        try:
            self._sync_browser_tip_messages()
            refresh_all_tip_surfaces(self)
        except Exception:
            logger.debug(t("ui-sync-browser-tip-messages-failed"), exc_info=True)

    def _sync_browser_tip_messages(self) -> None:
        """Set TipSurface messages from session state (never embed tip_line in other Statics)."""
        try:
            share = self.query_one("#summary-share-tip", TipSurface)
            share.set_tip(U.tip_share_url())
        except Exception:
            pass
        try:
            analysis_tip = self.query_one("#report-analysis-tip", TipSurface)
            if not self._report_plugin_ids() and (not self._active_plugin_results()):
                analysis_tip.set_tip(U.tip_no_analysis())
            else:
                analysis_tip.clear_message()
        except Exception:
            pass
        try:
            flags_tip = self.query_one("#report-flags-tip", TipSurface)
            if self._flags:
                flags_tip.clear_message()
            else:
                flags_tip.set_tip(U.tip_no_flags())
        except Exception:
            pass

    def _update_findings_header(self) -> None:
        """Findings tab counts only — tip lives in #findings-tip TipSurface."""
        fh = Text()
        fh.append(U.findings_heading() + "\n", style="bold")
        n = len(self._findings)
        high = sum(1 for f in self._findings if f.severity.value == "high")
        fh.append("\n  ")
        if high:
            fh.append_text(status_chip(f"{high}{t('ui-high-4')}", kind="bad"))
        elif n:
            fh.append_text(status_chip(f"{n}{t('ui-findings-2')}", kind="unknown"))
        else:
            fh.append_text(status_chip("none", kind="ok"))
        fh.append(f"  ·  {n}{t('ui-total')}", style="dim")
        self.query_one("#findings-header", Static).update(fh)

    def _update_summary_tab(self) -> None:
        if not self.meta:
            return
        asst = assistant_text_from_timeline(self.timeline)
        renderable = render_session_summary(self.meta, self.timeline, assistant_text=asst)
        try:
            widget = self.query_one("#summary-content", Static)
            widget.update(renderable)
        except Exception:
            pass
        try:
            self._sync_browser_tip_messages()
        except Exception:
            pass

    def _update_diff_tab(self) -> None:
        """Workspace diff: UI chrome structured; body MD when it is MD (usual)."""
        body = self._diff_md or ""
        meta = self._diff_meta or {}
        if len(body) > DIFF_TRUNCATE_THRESHOLD:
            body = (
                body[:DIFF_TRUNCATE_HEAD]
                + t("ui-truncated-see-rewind-points-jsonl-updates")
                + body[-DIFF_TRUNCATE_TAIL:]
            )
        try:
            widget = self.query_one("#diff-content", Static)
        except Exception:
            return
        head = Text()
        head.append(t("ui-diff-1"), style="bold")
        head.append_text(kv_line(t("ui-source"), str(meta.get("source") or "none")))
        extra = format_diff_meta_line(meta)
        if extra:
            head.append(f"  {extra}\n", style="dim")
        blocks: list = [head, dim_rule(), section_header(t("ui-changes"))]
        if body.strip():
            blocks.append(content_block(body, max_chars=DIFF_TRUNCATE_THRESHOLD))
        else:
            blocks.append(Text(t("ui-no-diff-data"), style="dim"))
        try:
            widget.update(panel_group(*blocks))
        except Exception:
            widget.update(body or t("ui-no-diff-data-1"))

    def _session_id(self) -> str:
        if self.meta and self.meta.session_id:
            return self.meta.session_id
        return self.session_dir.name

    def _reports_dir(self) -> Path:
        """Export dir for finding reports."""
        wd = getattr(self.app, "work_dir", None)
        base = Path(wd) if wd is not None else APP_HOME
        return base / "reports"

    _PLUGIN_TITLES: dict[str, str] = {
        "engine": t("ui-detectors"),
        "basic": t("ui-basic"),
        "feedback": t("ui-feedback"),
        "noop": t("ui-noop"),
    }

    @classmethod
    def _plugin_title(cls, aid: str) -> str:
        return cls._PLUGIN_TITLES.get(aid, aid.replace("_", " ").title())

    def _plugin_has_report_content(self, aid: str, result: AnalysisResult | None) -> bool:
        """True if the Report filter / section is worth listing (not an empty ok run)."""
        if aid == "noop":
            return False
        if any((f.plugin_id or "") == aid for f in self._findings):
            return True
        if result is None:
            return False
        if (result.error or "").strip():
            return True
        for val in (result.artifacts or {}).values():
            if str(val).strip():
                return True
        return False

    def _report_plugin_ids(self) -> list[str]:
        """Analyzer ids with real report content (enabled only; skips empty runs)."""
        ids: set[str] = set()
        for aid, result in self._active_plugin_results().items():
            if self._plugin_has_report_content(aid, result):
                ids.add(aid)
        for f in self._findings:
            pid = (f.plugin_id or "").strip()
            if pid and pid != "noop":
                ids.add(pid)
        return sorted(ids)

    @staticmethod
    def _report_plugin_slug(aid: str) -> str:
        """DOM-safe fragment for section / widget ids."""
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in aid) or "plugin"

    def _report_section_dom_id(self, key: str) -> str:
        if key == "flags":
            return "report-section-flags"
        return f"report-section-plugin-{self._report_plugin_slug(key)}"

    def _report_content_dom_id(self, key: str) -> str:
        if key == "flags":
            return "report-flags-content"
        return f"report-content-plugin-{self._report_plugin_slug(key)}"

    def _report_filter_options(self) -> list[tuple[str, str]]:
        """Select options: All, Flags (if any), then plugins that have content."""
        opts: list[tuple[str, str]] = [(U.all_sections(), "all")]
        n_flags = len(self._flags)
        if n_flags:
            opts.append((f"{t('ui-flags-1')}{n_flags})", "flags"))
        for aid in self._report_plugin_ids():
            n = sum(1 for f in self._findings if (f.plugin_id or "") == aid)
            label = self._plugin_title(aid)
            if n:
                label = f"{label} ({n})"
            else:
                label = f"{label}{t('ui-report')}"
            opts.append((label, f"plugin:{aid}"))
        return opts

    def _sync_report_view_select(self) -> None:
        """Refresh Report Filter dropdown options when plugins/findings change."""
        options = self._report_filter_options()
        key = tuple((f"{lab}\x00{val}" for lab, val in options))
        if key == self._report_select_options_key:
            return
        self._report_select_options_key = key
        try:
            sel = self.query_one("#report-view-select", Select)
        except Exception:
            return
        current = self._report_filter or "all"
        valid = {v for _, v in options}
        if current not in valid:
            current = "all"
            self._report_filter = "all"
        prev = self._report_updating
        self._report_updating = True
        try:
            sel.set_options(options)
            if sel.value != current:
                sel.value = current
        except Exception:
            logger.debug(t("ui-report-view-select-sync-failed"), exc_info=True)
        finally:
            self._report_updating = prev

    def _ensure_report_sections(self) -> None:
        """Mount inline panel-cards per plugin (idempotent); no checkbox row."""
        try:
            host = self.query_one("#report-sections-host", Vertical)
        except Exception:
            return
        self._report_section_keys.add("flags")
        for aid in self._report_plugin_ids():
            if aid in self._report_section_keys:
                continue
            section_id = self._report_section_dom_id(aid)
            content_id = self._report_content_dom_id(aid)
            card = Vertical(classes=t("ui-panel-card-report-section"), id=section_id)
            card.compose_add_child(Static(id=content_id))
            try:
                host.mount(card)
                self._report_section_keys.add(aid)
            except Exception:
                logger.debug(t("ui-failed-to-mount-report-section-s"), aid, exc_info=True)
        self._sync_report_view_select()
        self._apply_report_visibility()

    def _section_visible(self, key: str) -> bool:
        """Whether section *key* (flags | plugin id) is shown for current filter."""
        mode = self._report_filter or "all"
        if mode == "all":
            return True
        if mode == "flags":
            return key == "flags"
        if mode.startswith("plugin:"):
            return key == mode[7:]
        return True

    def _apply_report_visibility(self) -> None:
        """Show/hide inline sections from exclusive ``_report_filter`` (display only)."""
        for key in self._report_section_keys:
            section_id = self._report_section_dom_id(key)
            try:
                section = self.query_one(f"#{section_id}")
                section.display = self._section_visible(key)
            except Exception:
                pass

    def _set_static_content(self, widget_id: str, renderable) -> None:
        try:
            self.query_one(f"#{widget_id}", Static).update(renderable)
        except Exception:
            logger.debug(t("ui-report-static-s-missing"), widget_id, exc_info=True)

    def _update_reports_tab(self) -> None:
        """Fill overview + each inline section; Filter Select controls display."""
        if self._report_updating:
            return
        self._report_updating = True
        try:
            self._ensure_report_sections()
            self._render_report_overview()
            self._render_report_flags()
            for aid in self._report_plugin_ids():
                if aid in self._report_section_keys:
                    self._render_report_plugin(aid)
        finally:
            self._report_updating = False

    def _render_report_overview(self) -> None:
        sid = self._session_id()
        model = self.meta.model_id if self.meta else "unknown"
        flags = self._flags
        total = len(self._findings)
        high = sum(1 for f in self._findings if f.severity.value == "high")
        med = sum(1 for f in self._findings if f.severity.value == "medium")
        blocks: list = []
        head = Text()
        head.append(t("ui-session-report"), style="bold")
        head.append("\n")
        if high:
            head.append_text(status_chip(f"{high}{t('ui-high-4')}", kind="bad"))
            head.append("  ")
        elif total:
            head.append_text(status_chip(f"{total}{t('ui-findings-2')}", kind="unknown"))
            head.append("  ")
        else:
            head.append_text(status_chip("clean", kind="ok"))
            head.append("  ")
        head.append(f"{len(flags)}{t('ui-flags')}", style="dim")
        head.append(" │ ", style="dim")
        head.append(
            f"{total}{t('ui-findings-3')}{high}{t('ui-high-2')}{med}{t('ui-med-1')}", style="dim"
        )
        head.append("\n")
        blocks.append(head)
        blocks.append(dim_rule())
        meta_t = Text()
        meta_t.append_text(kv_line(t("ui-session-2"), sid))
        meta_t.append_text(kv_line(t("ui-model"), model or "—"))
        if self.meta and (self.meta.turn_outcome or "").strip():
            meta_t.append_text(
                kv_line(
                    t("ui-last-outcome"),
                    (self.meta.turn_outcome or "").strip()
                    + t("ui-session-meta-last-turn-interactive-gate"),
                )
            )
        blocks.append(meta_t)
        try:
            from ...session.turns import segment_timeline_turns

            segs = segment_timeline_turns(self.timeline)
            if segs:
                turns_t = Text()
                turns_t.append_text(section_header(t("ui-turns-1")))
                turns_t.append(
                    t("ui-findings-below-are-session-wide-use-event-indice"), style="dim"
                )
                for seg in segs:
                    turns_t.append_text(
                        bullet(
                            f"{seg.label}: {seg.event_count}{t('ui-events-2')}{seg.tool_call_count}{t('ui-tools-1')}"
                            + (
                                f" ({seg.tool_error_count}{t('ui-tool-err-1')}"
                                if seg.tool_error_count
                                else ""
                            )
                            + (
                                f"{t('ui-timeline')}{seg.first_index}–#{seg.last_index}"
                                if seg.first_index is not None
                                else ""
                            )
                        )
                    )
                blocks.append(turns_t)
        except Exception:
            pass
        if self._report_filter and self._report_filter != "all":
            mode = self._report_filter
            if mode == "flags":
                focus = t("ui-flags-2")
            elif mode.startswith("plugin:"):
                focus = self._plugin_title(mode[7:])
            else:
                focus = mode
            blocks.append(Text(f"{t('ui-viewing')}{focus}\n", style="dim"))
        try:
            self._set_static_content("report-overview-content", panel_group(*blocks))
        except Exception:
            self._set_static_content("report-overview-content", t("ui-report-unavailable"))
        try:
            self._sync_browser_tip_messages()
        except Exception:
            pass

    def _render_report_flags(self) -> None:
        flags = sorted(self._flags.values(), key=lambda fl: fl.event_index)
        fl_t = Text()
        fl_t.append_text(section_header(U.flags_heading()))
        fl_t.append(f"  {U.flags_blurb()}\n", style="dim")
        if flags:
            for fl in flags:
                ver = fl.verdict.value.replace("_", " ")
                tool = fl.tool_name or fl.event_type or "event"
                note = fl.description or t("ui-no-note")
                fl_t.append_text(bullet(f"#{fl.event_index}  {tool}  ·  {ver}  — {note}"))
                if fl.created_at:
                    fl_t.append(f"      {fl.created_at}\n", style="dim")
        self._set_static_content("report-flags-content", fl_t)
        try:
            self._sync_browser_tip_messages()
        except Exception:
            pass

    def _render_report_plugin(self, aid: str) -> None:
        content_id = self._report_content_dom_id(aid)
        plugin_findings = [f for f in self._findings if (f.plugin_id or "") == aid]
        result = self._active_plugin_results().get(aid)
        if result is None and aid not in self._active_plugin_results():
            return
        blocks: list = []
        title = self._plugin_title(aid)
        if result is not None and result.summary:
            title = f"{title}  ({result.summary})"
        blocks.append(section_header(title))
        if plugin_findings:
            blocks.append(self._findings_report_block(plugin_findings))
        elif result is not None:
            report_artifact = result.artifacts.get("report") if result.ok else None
            if report_artifact and str(report_artifact).strip():
                blocks.append(content_block(str(report_artifact), max_chars=12000))
            elif result.summary:
                blocks.append(Text(f"  {result.summary}\n", style="dim"))
            elif not result.ok and result.error:
                blocks.append(Text(f"{t('ui-error-2')}{result.error}\n", style="red"))
            else:
                blocks.append(Text(t("ui-no-findings"), style="dim"))
        else:
            blocks.append(Text(t("ui-no-findings"), style="dim"))
        try:
            self._set_static_content(content_id, panel_group(*blocks))
        except Exception:
            self._set_static_content(content_id, t("ui-report-unavailable"))

    @on(Select.Changed, "#report-view-select")
    def _on_report_view_changed(self, event: Select.Changed) -> None:
        """Exclusive section filter — same pattern as Timeline View Select."""
        if self._report_updating:
            return
        val = event.value
        if val is Select.BLANK or val is None:
            return
        mode = str(val)
        if mode == self._report_filter:
            return
        self._report_filter = mode
        self._apply_report_visibility()
        try:
            self._render_report_overview()
        except Exception:
            pass

    @staticmethod
    def _findings_report_block(findings: list) -> Text:
        """Structured finding list (severity + title + detail) — not raw markdown dump."""
        out = Text()
        for f in sorted(findings, key=lambda x: (x.severity.value, x.title or "")):
            sev = (f.severity.value if f.severity else "low").upper()
            sev_style = (
                "bold red"
                if sev == t("ui-high-3")
                else "bold yellow"
                if sev == t("ui-medium")
                else "dim"
            )
            out.append("  ")
            out.append(f"{sev:<7}", style=sev_style)
            out.append("  ")
            out.append(f.title or f.id or "(untitled)")
            cat = getattr(f, "category", None) or ""
            if cat:
                out.append(f"  ·  {cat}", style="dim")
            n_ev = len(getattr(f, "all_tool_call_ids", None) or [])
            if n_ev:
                out.append(f"  ·  {n_ev}{t('ui-events-1')}", style="dim")
            out.append("\n")
            detail = (getattr(f, "detail", None) or "").strip()
            if detail:
                one_line = " ".join(detail.split())
                if len(one_line) > 220:
                    one_line = one_line[:217] + "…"
                out.append(f"           {one_line}\n", style="dim")
            children = getattr(f, "children", None) or []
            for ch in children[:8]:
                ch_title = getattr(ch, "title", None) or getattr(ch, "id", "") or ""
                out.append(f"           - {ch_title}\n", style="dim")
            if len(children) > 8:
                out.append(f"           … +{len(children) - 8}{t('ui-more')}", style="dim")
        if not findings:
            out.append(t("ui-none"), style="dim")
        return out

    def _update_stats(self) -> None:
        """Stats: header chrome + DataTables (same zebra/frame as Findings)."""
        if not self.meta:
            return
        m = self.meta
        type_counts = Counter(e.event_type for e in self.timeline)
        error_count = sum(1 for e in self.timeline if e.is_error)
        timeline_table = self.query_one("#timeline-list", TimelineTable)
        durations = timeline_table.durations
        tool_call_events = [e for e in self.timeline if e.event_type == "tool_call" and e.tool_name]
        tool_counts: Counter[str] = Counter(e.tool_name for e in tool_call_events)
        tool_errors: Counter[str] = Counter(e.tool_name for e in tool_call_events if e.is_error)
        tool_durations: dict[str, list[float]] = defaultdict(list)
        for e in tool_call_events:
            dur = durations.get(e.index)
            if dur is not None:
                tool_durations[e.tool_name].append(dur)
        outcome = (m.turn_outcome or "unknown").strip()
        kind = "unknown"
        oc = outcome.lower()
        if oc in ("success", "ok", "completed", "complete"):
            kind = "ok"
        elif m.turn_failed or oc in ("error", "failed", "failure", "timeout"):
            kind = "bad"
        turn_segments = []
        try:
            from ...session.turns import segment_timeline_turns, turn_summary_rows

            turn_segments = segment_timeline_turns(self.timeline)
            turn_rows = turn_summary_rows(turn_segments, durations=durations)
        except Exception:
            turn_rows = []
        head = Text()
        head.append(t("ui-statistics"), style="bold")
        head.append("\n  ")
        head.append_text(status_chip(outcome, kind=kind))
        head.append("\n")
        strip_bits = [
            m.model_id or "—",
            m.duration_str or "—",
            f"{len(self.timeline)}{t('ui-events-1')}",
            f"{error_count}{t('ui-errors-1')}",
            f"{len(self._findings)}{t('ui-findings-2')}",
        ]
        if turn_segments:
            strip_bits.insert(2, f"{len(turn_segments)}{t('ui-turns')}")
        head.append_text(meta_strip(strip_bits))
        meta_t = Text()
        meta_t.append_text(kv_line(t("ui-session-2"), m.session_id or "—"))
        if m.title:
            meta_t.append_text(kv_line(t("ui-title"), m.title))
        meta_t.append_text(kv_line(t("ui-model"), m.model_id or "—"))
        if turn_segments:
            meta_t.append_text(kv_line(t("ui-turns-1"), str(len(turn_segments))))
            last_seg = turn_segments[-1]
            meta_t.append_text(kv_line(t("ui-last-turn"), last_seg.label))
        meta_t.append_text(kv_line(t("ui-loops"), str(m.loop_count or "—")))
        meta_t.append_text(kv_line(t("ui-duration"), m.duration_str or "—"))
        meta_t.append_text(
            kv_line(t("ui-outcome"), f"{outcome}{t('ui-last-turn-gate-see-turns-table')}")
        )
        diff_line = format_diff_meta_line(self._diff_meta or {})
        if diff_line:
            meta_t.append_text(kv_line(t("ui-diff-2"), diff_line))
        meta_t.append_text(
            kv_line(
                t("ui-findings-1"),
                f"{len(self._findings)}{t('ui-from')}{len(self.plugin_results)}{t('ui-plugins-1')}",
            )
        )
        try:
            from ...runs.live_share import get_share_display

            info = get_share_display(self.session_dir)
            share_raw = info.get("share_url")
            url = str(share_raw).strip() if share_raw else ""
            if url:
                meta_t.append_text(kv_line(t("ui-share"), url))
            elif info.get("pending"):
                meta_t.append_text(kv_line(t("ui-share"), "pending"))
        except Exception:
            pass
        try:
            self.query_one("#stats-header", Static).update(panel_group(head, dim_rule(), meta_t))
        except Exception:
            pass
        try:
            turns_table = self.query_one("#stats-turns-table", DataTable)
            style_data_table(turns_table)
            turns_table.clear(columns=True)
            turns_table.add_columns(
                "#",
                t("ui-label"),
                t("ui-outcome"),
                t("ui-events"),
                t("ui-tools"),
                t("ui-tool-err"),
                t("ui-user"),
                t("ui-asst"),
                t("ui-dur"),
                t("ui-top-tools"),
                t("ui-span"),
            )
            if turn_rows:
                for row in turn_rows:
                    dur_raw = row.get("duration_s")
                    dur_s = (
                        self._fmt_dur(float(dur_raw)) if isinstance(dur_raw, (int, float)) else "—"
                    )
                    fi, li = (row.get("first_index"), row.get("last_index"))
                    span = f"#{fi}–#{li}" if fi is not None and li is not None else "—"
                    turns_table.add_row(
                        str(row.get("turn", "")),
                        str(row.get("label", "")),
                        str(row.get("outcome", "—")),
                        str(row.get("events", 0)),
                        str(row.get("tools", 0)),
                        str(row.get("tool_errors", 0) or "—"),
                        str(row.get("users", 0)),
                        str(row.get("assistants", 0)),
                        dur_s,
                        str(row.get("top_tools", "—"))[:40],
                        span,
                        key=f"turn-{row.get('turn')}",
                    )
            else:
                turns_table.add_row(
                    "—", t("ui-no-timeline"), "—", "0", "0", "—", "0", "0", "—", "—", "—"
                )
        except Exception:
            pass
        ev_table = self.query_one("#stats-events-table", DataTable)
        style_data_table(ev_table)
        ev_table.clear(columns=True)
        ev_table.add_columns(U.col_event_type(), U.col_count())
        for etype, count in type_counts.most_common():
            ev_table.add_row(etype, str(count))
        if not type_counts:
            ev_table.add_row("(none)", "0")
        tool_cat: dict[str, str] = {}
        usage = None
        _append_usage_rich = None
        try:
            from ...session.usage_stats import collect_session_usage
            from ..session_summary import append_usage_rich as _aur

            _append_usage_rich = _aur
            usage = collect_session_usage(self.session_dir, self.timeline, durations=durations)
            tool_cat = {r.name: r.category for r in usage.tools}
        except Exception:
            pass

        def _tool_sort_key(item: tuple[str, int]) -> tuple[int, int, str]:
            name, cnt = item
            cat = tool_cat.get(name, "")
            tier = 0 if cat == "builtin" or not cat else 1 if cat == "mcp_bridge" else 2
            return (tier, -cnt, name)

        tools_table = self.query_one("#stats-tools-table", DataTable)
        style_data_table(tools_table)
        tools_table.clear(columns=True)
        tools_table.add_columns(
            t("ui-tool-1"),
            t("ui-calls"),
            t("ui-errors-2"),
            t("ui-total-1"),
            t("ui-avg"),
            t("ui-min"),
            t("ui-max"),
            t("ui-kind"),
        )
        for tool, count in sorted(tool_counts.items(), key=_tool_sort_key):
            errs = tool_errors.get(tool, 0)
            durs = tool_durations.get(tool, [])
            if durs:
                total_s = self._fmt_dur(sum(durs))
                avg_s = self._fmt_dur(sum(durs) / len(durs))
                mn_s = self._fmt_dur(min(durs))
                mx_s = self._fmt_dur(max(durs))
            else:
                total_s = avg_s = mn_s = mx_s = "—"
            cat = tool_cat.get(tool, "")
            if cat == "mcp_bridge":
                kind_s = t("ui-mcp-bridge")
            elif cat and cat != "builtin":
                kind_s = cat
            else:
                kind_s = "host"
            tools_table.add_row(
                tool, str(count), str(errs) if errs else "—", total_s, avg_s, mn_s, mx_s, kind_s
            )
        all_durs = [d for dlist in tool_durations.values() for d in dlist]
        if all_durs:
            tools_table.add_row(
                t("ui-total-2"),
                str(len(tool_call_events)),
                str(sum(tool_errors.values()) or "—"),
                self._fmt_dur(sum(all_durs)),
                "—",
                "—",
                "—",
                "",
            )
        phase_durations: dict[str, float] = defaultdict(float)
        phase_labels = {
            "thought": t("ui-thinking"),
            "assistant": t("ui-writing"),
            "tool_call": t("ui-tool-execution"),
            "tool_result": t("ui-tool-execution"),
            "user": t("ui-user-input"),
            "plan": t("ui-planning"),
            "subagent": t("ui-subagent"),
        }
        for ev in self.timeline:
            dur = durations.get(ev.index)
            if dur is None:
                continue
            label = phase_labels.get(ev.event_type, t("ui-other"))
            phase_durations[label] += dur
        phases_table = self.query_one("#stats-phases-table", DataTable)
        style_data_table(phases_table)
        phases_table.clear(columns=True)
        phases_table.add_columns(U.col_activity(), U.col_time(), U.col_percent())
        if phase_durations:
            total_accounted = sum(phase_durations.values())
            for label, secs in sorted(phase_durations.items(), key=lambda x: -x[1]):
                pct = secs / total_accounted * 100 if total_accounted else 0
                phases_table.add_row(label, self._fmt_dur(secs), f"{pct:.1f}%")
            phases_table.add_row("total", self._fmt_dur(total_accounted), "100%")
            if m.duration_seconds and total_accounted < m.duration_seconds:
                unaccounted = m.duration_seconds - total_accounted
                phases_table.add_row("overhead", self._fmt_dur(unaccounted), "—")
        else:
            phases_table.add_row("(none)", "—", "—")
        try:
            usage_w = self.query_one("#stats-usage", Static)
            if usage is not None and _append_usage_rich is not None:
                usage_t = Text()
                _append_usage_rich(usage_t, usage)
                usage_w.update(usage_t if usage_t.plain.strip() else "")
            else:
                usage_w.update("")
        except Exception:
            pass

    @on(TimelineTable.EventSelected)
    def _on_event_selected(self, message: TimelineTable.EventSelected) -> None:
        ev = message.event
        self._current_event = ev
        detail = self.query_one("#detail-panel", DetailView)
        timeline_table = self.query_one("#timeline-list", TimelineTable)
        finding = self._findings_by_call.get(ev.tool_call_id)
        duration = timeline_table.durations.get(ev.index)
        flag = self._flags.get(ev.index)
        detail.show_event(
            ev,
            finding,
            flag,
            duration=duration,
            paired_call=timeline_table.get_paired_call(ev),
            paired_result=timeline_table.get_paired_result(ev),
        )
        self.refresh_bindings()

    def on_descendant_focus(self, _event) -> None:
        self.refresh_bindings()

    def on_descendant_blur(self, _event) -> None:
        self.refresh_bindings()

    def action_refresh_context(self) -> None:
        """Reload timeline/meta for this session and re-run analysis."""
        self.notify(U.refreshing_session_view(), severity="information", timeout=3)
        self._load_data()
        try:
            self._update_summary_tab()
            self._update_stats()
        except Exception:
            pass

    def action_open_share(self) -> None:
        """Open Grok share URL for this session (from groket-share.json) in the browser."""
        try:
            from ...runs.live_share import get_share_display, refresh_share_from_disk

            url = refresh_share_from_disk(self.session_dir)
            info = get_share_display(self.session_dir)
            if not url:
                _ = info
                return
            try:
                import webbrowser

                webbrowser.open(url)
            except Exception as exc:
                self.notify(U.could_not_open_share(str(exc)), severity="error", timeout=10)
        except Exception as exc:
            self.notify(U.share_failed(str(exc)), severity="error")

    _TIMELINE_VIEWS: tuple[str, ...] = ("all", "tools", "user", "asst", "sess", "errors")

    def _sync_timeline_view_select(self, mode: str) -> None:
        try:
            sel = self.query_one("#timeline-view-select", Select)
            if sel.value != mode:
                sel.value = mode
        except Exception:
            pass

    def _ensure_timeline_tab(self) -> None:
        """Timeline view only applies on pane 1 — switch there first."""
        try:
            tabs = self.query_one("#browser-tabs", TabbedContent)
            if tabs.active != "tab-timeline":
                self._activate_browser_tab("tab-timeline")
        except Exception:
            self._activate_browser_tab("tab-timeline")

    def _apply_timeline_mode(self, mode: str) -> None:
        """Apply View dropdown mode; keyboard and Select stay aligned."""
        if mode not in self._TIMELINE_VIEWS:
            mode = "all"
        self._ensure_timeline_tab()
        self._timeline_filter = mode
        self._errors_only = mode == "errors"
        self._sync_timeline_view_select(mode)
        if mode == "all":
            self._apply_filter(errors_only=False)
        elif mode == "tools":
            self._apply_filter(event_type="tool_call", errors_only=False)
        elif mode == "user":
            self._apply_filter(event_type="user", errors_only=False)
        elif mode == "asst":
            self._apply_filter(event_type="assistant", errors_only=False)
        elif mode == "sess":
            self._apply_filter(event_types={"session", "session_error"}, errors_only=False)
        elif mode == "errors":
            self._apply_filter(errors_only=True)

        def _focus_tl() -> None:
            try:
                focus_primary_list(self.query_one("#timeline-list", TimelineTable))
            except Exception:
                pass

        self.call_after_refresh(lambda: self.call_after_refresh(_focus_tl))

    def action_focus_timeline_filter(self) -> None:
        """``v`` — focus the View select (open with Enter / arrows)."""
        self._ensure_timeline_tab()

        def _focus() -> None:
            try:
                self.query_one("#timeline-view-select", Select).focus()
            except Exception:
                pass

        self.call_after_refresh(lambda: self.call_after_refresh(_focus))

    @on(Select.Changed, "#timeline-view-select")
    def _on_timeline_view_changed(self, event: Select.Changed) -> None:
        val = event.value
        if val is Select.BLANK or val is None:
            return
        mode = str(val)
        if mode == self._timeline_filter:
            return
        self._timeline_filter = mode
        self._errors_only = mode == "errors"
        if mode == "all":
            self._apply_filter(errors_only=False)
        elif mode == "tools":
            self._apply_filter(event_type="tool_call", errors_only=False)
        elif mode == "user":
            self._apply_filter(event_type="user", errors_only=False)
        elif mode == "asst":
            self._apply_filter(event_type="assistant", errors_only=False)
        elif mode == "sess":
            self._apply_filter(event_types={"session", "session_error"}, errors_only=False)
        elif mode == "errors":
            self._apply_filter(errors_only=True)

    def _finding_row_index(
        self, event: DataTable.RowHighlighted | DataTable.RowSelected
    ) -> int | None:
        """Map findings-table row event → index in _findings_table_entries.

        Row keys are normally ``"0"``, ``"1"``, … (index into ``_findings_table_entries``).
        Older/in-flight TUI builds briefly used ``i-{rule_id}-{n}`` / ``c-{id}-{n}``; never
        ``int()`` those blindly — fall back to cursor row or suffix digit.
        """
        if event.row_key is not None:
            raw = str(event.row_key.value).strip()
            if raw.isdigit():
                idx = int(raw)
                if 0 <= idx < len(self._findings_table_entries):
                    return idx
            if "-" in raw:
                tail = raw.rsplit("-", 1)[-1]
                if tail.isdigit():
                    idx = int(tail)
                    if 0 <= idx < len(self._findings_table_entries):
                        return idx
        try:
            table = self.query_one("#findings-table", DataTable)
            cr = getattr(table, "cursor_row", None)
            if cr is not None and 0 <= int(cr) < len(self._findings_table_entries):
                return int(cr)
        except Exception:
            pass
        return None

    @on(DataTable.RowHighlighted, "#findings-table")
    def _on_finding_highlighted(self, event: DataTable.RowHighlighted) -> None:
        try:
            idx = self._finding_row_index(event)
            if idx is not None and idx < len(self._findings_table_entries):
                self._selected_finding = self._findings_table_entries[idx]
        except Exception:
            pass

    @on(DataTable.RowSelected, "#findings-table")
    def _on_finding_selected(self, event: DataTable.RowSelected) -> None:
        try:
            idx = self._finding_row_index(event)
        except Exception:
            return
        if idx is None or idx >= len(self._findings_table_entries):
            return
        finding = self._findings_table_entries[idx]
        self._selected_finding = finding
        call_ids = set(finding.all_tool_call_ids)
        update_indices = set(finding.all_update_indices)
        timeline_table = self.query_one("#timeline-list", TimelineTable)
        timeline_table.apply_filter(call_ids=call_ids, update_indices=update_indices)
        tabbed = self.query_one(TabbedContent)
        tabbed.active = "tab-timeline"

    @on(Input.Submitted, "#search-input")
    def _on_search(self, event: Input.Submitted) -> None:
        self._apply_filter(search_query=event.value)

    def _turn_event_indices(self) -> set[int] | None:
        """Event indices for the Turn filter, or None for all turns."""
        tf = getattr(self, "_turn_filter", "all")
        if tf in (None, "", "all"):
            return None
        try:
            ti = int(tf)
        except (TypeError, ValueError):
            return None
        for seg in getattr(self, "_turn_segments", None) or []:
            if seg.turn_index == ti:
                return {e.index for e in seg.events}
        return None

    def _rebuild_turn_select(self) -> None:
        """Refresh Turn dropdown; hide it for single-turn (or empty) sessions."""
        from ...session.turns import segment_timeline_turns

        self._turn_segments = segment_timeline_turns(self.timeline)
        multi = len(self._turn_segments) > 1
        try:
            sel = self.query_one("#timeline-turn-select", Select)
        except Exception:
            return
        if not multi:
            # No choice to make — keep filter off and hide the control.
            self._turn_filter = "all"
            sel.display = False
            return
        options: list[tuple[str, str]] = [(t("turn-filter-all"), "all")]
        for seg in self._turn_segments:
            options.append((t("turn-filter-n", n=seg.turn_index), str(seg.turn_index)))
        sel.display = True
        sel.set_options(options)
        if getattr(self, "_turn_filter", "all") not in {v for _, v in options}:
            self._turn_filter = "all"
        sel.value = getattr(self, "_turn_filter", "all")

    def _apply_filter(self, **kwargs) -> None:
        timeline_table = self.query_one("#timeline-list", TimelineTable)
        if "event_indices" not in kwargs:
            kwargs["event_indices"] = self._turn_event_indices()
        timeline_table.apply_filter(**kwargs)

    @on(Select.Changed, "#timeline-turn-select")
    def _on_timeline_turn_changed(self, event: Select.Changed) -> None:
        val = event.value
        if val is Select.BLANK or val is None:
            return
        self._turn_filter = str(val)
        # Re-apply current type filter with new turn slice
        mode = getattr(self, "_timeline_filter", "all")
        self._timeline_filter = ""  # force re-apply
        self._apply_timeline_mode(mode)


    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_search(self) -> None:
        self._ensure_timeline_tab()

        def _focus_search() -> None:
            try:
                self.query_one("#search-input", Input).focus()
            except Exception:
                pass

        self.call_after_refresh(lambda: self.call_after_refresh(_focus_search))

    def action_clear_filters(self) -> None:
        try:
            self.query_one("#search-input", Input).value = ""
        except Exception:
            pass
        self._apply_timeline_mode("all")

    def action_show_findings(self) -> None:
        """Jump to Findings (same as tab 2 / ``i``)."""
        self.action_tab_findings()

    def _browser_tab_index(self) -> int:
        try:
            active = self.query_one("#browser-tabs", TabbedContent).active
        except Exception:
            return 0
        for i, (pane_id, _) in enumerate(_BROWSER_TABS):
            if pane_id == active:
                return i
        return 0

    def _activate_browser_tab(self, pane_id: str, *, focus_selector: str | None = None) -> None:
        """Switch pane, then focus its primary widget after layout."""
        if focus_selector is None:
            for pid, sel in _BROWSER_TABS:
                if pid == pane_id:
                    focus_selector = sel
                    break
        try:
            tabbed = self.query_one("#browser-tabs", TabbedContent)
            tabbed.active = pane_id
        except Exception:
            try:
                self.query_one(TabbedContent).active = pane_id
            except Exception:
                return
        if not focus_selector:
            return
        sel = focus_selector

        def _focus() -> None:
            try:
                w = self.query_one(sel)
            except Exception:
                return
            focus_primary_list(w)

        self.call_after_refresh(lambda: self.call_after_refresh(_focus))

    def action_tab_next(self) -> None:
        """Cycle to the next session tab (``]``) — primary navigation affordance."""
        i = (self._browser_tab_index() + 1) % len(_BROWSER_TABS)
        pane_id, sel = _BROWSER_TABS[i]
        self._activate_browser_tab(pane_id, focus_selector=sel)

    def action_tab_prev(self) -> None:
        """Cycle to the previous session tab (``[``)."""
        i = (self._browser_tab_index() - 1) % len(_BROWSER_TABS)
        pane_id, sel = _BROWSER_TABS[i]
        self._activate_browser_tab(pane_id, focus_selector=sel)

    def action_tab_timeline(self) -> None:
        self._activate_browser_tab("tab-timeline")

    def action_tab_findings(self) -> None:
        self._activate_browser_tab("tab-findings")

    def action_tab_summary(self) -> None:
        self._activate_browser_tab("tab-summary")

    def action_tab_diff(self) -> None:
        self._activate_browser_tab("tab-diff")

    def action_tab_report(self) -> None:
        self._activate_browser_tab("tab-reports")

    def action_tab_stats(self) -> None:
        self._activate_browser_tab("tab-stats")

    def _timeline_event_actionable(self) -> bool:
        """True when Flag (etc.) should be enabled: Timeline pane + focused list + event."""
        if self._current_event is None:
            return False
        try:
            tabs = self.query_one("#browser-tabs", TabbedContent)
            if tabs.active != "tab-timeline":
                return False
        except Exception:
            return False
        try:
            tl = self.query_one("#timeline-list", TimelineTable)
            focused = self.app.focused
            if focused is None:
                return False
            if focused is tl:
                return True
            parent = getattr(focused, "parent", None)
            while parent is not None:
                if parent is tl:
                    return True
                parent = getattr(parent, "parent", None)
        except Exception:
            return False
        return False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Hide Flag in the footer/bindings unless a timeline event is selected+focused."""
        if action == "flag_event":
            return True if self._timeline_event_actionable() else False
        if action == "export_finding":
            try:
                tabs = self.query_one("#browser-tabs", TabbedContent)
                on_findings = tabs.active == "tab-findings"
            except Exception:
                on_findings = False
            return bool(on_findings and self._selected_finding is not None)
        if action in ("send_follow_up", "mark_session_done", "focus_follow_up"):
            # Only when this session can accept a next prompt or end (interactive gate).
            try:
                from ...session.turn_gate import (
                    list_queued_follow_ups,
                    session_awaits_follow_up,
                )

                if session_awaits_follow_up(self.session_dir):
                    return True
                if list_queued_follow_ups(self.session_dir):
                    return True
            except Exception:
                pass
            return False
        return True

    def action_flag_event(self) -> None:
        """Open the flag modal for the currently selected timeline event."""
        if not self._timeline_event_actionable():
            return
        assert self._current_event is not None
        existing = self._flags.get(self._current_event.index)
        self.app.push_screen(
            FlagModal(self._current_event, existing_flag=existing), callback=self._on_flag_result
        )

    def _refresh_event_chrome(self) -> None:
        """Re-paint timeline Flags column + detail for the current event."""
        try:
            tl = self.query_one("#timeline-list", TimelineTable)
            tl.load_events(self.timeline, self._findings, list(self._flags.values()))
        except Exception:
            pass
        ev = self._current_event
        if ev is None:
            return
        try:
            detail = self.query_one("#detail-panel", DetailView)
            timeline_table = self.query_one("#timeline-list", TimelineTable)
            finding = self._findings_by_call.get(ev.tool_call_id)
            duration = timeline_table.durations.get(ev.index)
            detail.show_event(
                ev,
                finding,
                self._flags.get(ev.index),
                duration=duration,
                paired_call=timeline_table.get_paired_call(ev),
                paired_result=timeline_table.get_paired_result(ev),
            )
        except Exception:
            pass

    def _on_flag_result(self, result: tuple | None) -> None:
        """Handle save/delete from the FlagModal."""
        if result is None:
            return
        action, payload = result
        if action == "save":
            flag = payload
            self._flags[flag.event_index] = flag
            self.notify(U.flag_saved(flag.event_index))
        elif action == "delete":
            event_index = payload
            self._flags.pop(event_index, None)
            self.notify(U.flag_removed(event_index))
        save_flags(self.session_dir, list(self._flags.values()))
        self._refresh_event_chrome()
        self._update_reports_tab()

    def _report_finding(self, finding: Finding) -> None:
        """Generate a markdown report for a finding."""
        model = self.meta.model_id if self.meta else "unknown"
        session_id = self.meta.session_id if self.meta else "unknown"
        lines = [
            f"{t('ui-model-1')}{model}`",
            f"{t('ui-session')}{session_id}`",
            f"{t('ui-plugin')}{finding.plugin_id}`",
            f"{t('ui-finding-1')}{finding.id}`",
            f"{t('ui-severity')}{finding.severity.value.upper()}",
            f"{t('ui-category')}{finding.category}",
            "",
            f"**{finding.title}**",
        ]
        if finding.detail:
            lines.append("")
            for dl in finding.detail.strip().splitlines():
                lines.append(f"> {dl}")
        if finding.children:
            lines.append("")
            lines.append(f"*{len(finding.children)}{t('ui-sub-finding-s')}")
            for child in finding.children:
                lines.append(f"> [{child.severity.value.upper()}] `{child.id}`: {child.title[:80]}")
        if finding.extras.get("should_have"):
            lines.append("")
            lines.append(t("ui-what-the-model-should-have-done"))
            lines.append(f"> {finding.extras['should_have']}")
        filename = f"finding-{finding.plugin_id}-{finding.id}"
        report_text = "\n".join(lines)
        try:
            reports_dir = self._reports_dir()
            reports_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            report_file = reports_dir / f"{filename}-{ts}.md"
            with open(report_file, "w") as f:
                f.write(report_text)
            self.notify(U.report_saved(str(report_file)), severity="information")
        except Exception as exc:
            self.notify(U.report_failed(str(exc)), severity="error")

    def action_export_finding(self) -> None:
        """Export the selected finding to a markdown file."""
        tabbed = self.query_one(TabbedContent)
        if tabbed.active != "tab-findings" or self._selected_finding is None:
            self.notify(U.select_finding_first(), severity="warning")
            return
        self._report_finding(self._selected_finding)
