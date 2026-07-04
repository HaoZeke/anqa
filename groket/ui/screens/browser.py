"""Trace browser screen — interactive timeline with detail view and feedback."""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult

from ..data_table import style_data_table
from ..i18n import join_ui, t

logger = logging.getLogger(__name__)
from collections import Counter, defaultdict

from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from ... import event_types as et
from ...analysis import get_analysis_service
from ...analysis.base import AnalysisResult, Finding
from ...constants import DIFF_TRUNCATE_HEAD, DIFF_TRUNCATE_TAIL, DIFF_TRUNCATE_THRESHOLD
from ...flags import load_flags, save_flags
from ...models import Flag, SessionMeta, TraceEvent
from ...parser import load_session_meta, parse_timeline
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
    panel_group,
    refresh_all_tip_surfaces,
    section_header,
    status_chip,
)
from ..session_summary import assistant_text_from_timeline, render_session_summary
from ..styles import SEVERITY_LABEL, severity_style
from ..tab_panes import TabPaneNavigation
from ..threads import call_ui, resolve_ui_app
from ..widgets.controls import FILTER_BAR_CLASS, FILTER_LABEL_CLASS
from ..widgets.detail_view import DetailView
from ..widgets.flag_panel import FlagModal
from ..widgets.timeline import TimelineTable


class BrowserScreen(TabPaneNavigation, ChromeActions):
    """Interactive trace browser with timeline, detail view, and findings."""

    BINDINGS = list(BROWSER)
    TAB_CONTENT_ID = "browser-tabs"
    TAB_PANES = (
        ("tab-timeline", "#timeline-list"),
        ("tab-summary", "#summary-scroll"),
        ("tab-diff", "#diff-scroll"),
        ("tab-findings", "#findings-table"),
        ("tab-reports", "#reports-scroll"),
    )

    def action_tab_timeline(self) -> None:
        self.activate_tab_pane("tab-timeline")

    def action_tab_summary(self) -> None:
        self.activate_tab_pane("tab-summary")

    def action_tab_diff(self) -> None:
        self.activate_tab_pane("tab-diff")

    def action_tab_findings(self) -> None:
        self.activate_tab_pane("tab-findings")

    def action_tab_report(self) -> None:
        self.activate_tab_pane("tab-reports")

    def __init__(
        self, session_dir: Path, plugin_results: dict[str, AnalysisResult] | None = None, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.session_dir = session_dir
        self.meta: SessionMeta | None = None
        self.timeline: list[TraceEvent] = []
        self.plugin_results: dict[str, AnalysisResult] = plugin_results or {}
        self._analysis_stale_hints: list[str] = []
        self._analysis_pending: bool = False
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
        self._timeline_search: str = ""
        self._report_section_keys: set[str] = set()
        self._report_filter: str = "all"
        self._report_select_options_key: tuple[str, ...] = ()
        self._report_updating: bool = False
        self._live_refresh_timer: Timer | None = None
        self._live_heartbeat_timer: Timer | None = None
        self._analysis_spinner_timer: Timer | None = None
        self._trace_watch: object | None = None  # fs_watch stop handle
        self._last_light_fp: tuple[str | int | float | bool | None, ...] | None = None
        self._last_trace_mtime: float | None = None
        self._last_signals_mtime: float | None = None
        self._delete_pending: bool = False
        self._live_refresh_busy = False
        self._live_refresh_pending = False
        self._light_refresh_heartbeat = False
        from ...session.context_samples import ContextSampleStore

        self._context_samples = ContextSampleStore()

    def compose(self) -> ComposeResult:
        yield Header()
        from ..widgets.activity_bar import ActivityBar

        yield ActivityBar()
        yield Static("", id="analysis-stale-banner", classes="tip-surface")
        with Vertical(id="session-pending-bar"):
            yield Static("", id="session-pending-status")
            yield Static("", id="session-pending-queue")
            yield Input(placeholder=U.follow_up_placeholder_send(), id="session-follow-input")
            yield Checkbox(
                t("follow-up-last-turn"),
                id="session-follow-last-turn",
                value=False,
            )
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
            with TabPane(U.tab_summary(), id="tab-summary"):
                with VerticalScroll(id="summary-scroll"):
                    with Vertical(classes="panel-card"):
                        yield Static(id="summary-content")
                        yield TipSurface(U.tip_share_url(), id="summary-share-tip")
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
            with TabPane(U.tab_diff(), id="tab-diff"):
                with VerticalScroll(id="diff-scroll"):
                    with Vertical(classes="panel-card"):
                        yield Static(id="diff-content")
            with TabPane(U.tab_findings(), id="tab-findings"):
                with Vertical(id="findings-panel"):
                    with Vertical(classes="panel-card"):
                        yield Static("", id="findings-header")
                        yield TipSurface(U.tip_findings_row(), id="findings-tip")
                    with Vertical(classes=t("ui-panel-card-panel-card-grow")):
                        yield Static("", id="findings-pending-status")
                        yield DataTable(id="findings-table")
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
        self._stop_analysis_spinner_timer()
        self._stop_live_refresh()

    def _stop_live_refresh(self) -> None:
        for attr in ("_live_refresh_timer", "_live_heartbeat_timer"):
            t = getattr(self, attr, None)
            if t is not None:
                try:
                    t.stop()
                except Exception:
                    pass
            setattr(self, attr, None)
        w = self._trace_watch
        self._trace_watch = None
        stop = getattr(w, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass

    def _session_is_pending(self) -> bool:
        """True only for interactive multi-turn follow-up / Done UI.

        Single-turn evals still create a turn gate with ``state=running`` and never
        set ``awaiting_follow_up``; do not treat that as a follow-up bar.
        Show only when the gate is waiting for a prompt, the host has queued
        follow-ups, or the host has requested Done while the agent finishes.
        """
        from ...session.turn_gate import (
            host_requested_done,
            list_queued_follow_ups,
            read_turn_gate_status,
            session_awaits_follow_up,
        )

        try:
            st = read_turn_gate_status(self.session_dir)
        except Exception:
            st = {}
        gstate = str(st.get("state") or "")

        if gstate == "done":
            return False

        # Host asked to stop (interactive Done) while the agent may still be finishing.
        if host_requested_done(self.session_dir):
            return True

        if session_awaits_follow_up(self.session_dir):
            return True

        try:
            if list_queued_follow_ups(self.session_dir):
                return True
        except Exception:
            pass

        return False

    def _session_needs_live_timeline(self) -> bool:
        """True while the agent may still append traces (not idle follow-up wait).

        Interactive ``awaiting_follow_up`` keeps the pending bar, but does **not**
        need a full timeline reload every tick — only when files change or the
        agent is running / finishing Done.
        """
        from ...session.turn_gate import host_requested_done, read_turn_gate_status

        meta = self.meta
        oc = (meta.turn_outcome or "").lower().replace(" ", "_") if meta else ""
        if oc in ("running", "in_progress", "pending"):
            return True
        try:
            st = read_turn_gate_status(self.session_dir)
        except Exception:
            st = {}
        gstate = str(st.get("state") or "")
        if gstate == "done":
            return False
        if host_requested_done(self.session_dir):
            return True
        if gstate == "running":
            return True
        return False

    def _refresh_session_pending_bar(self) -> None:
        from ...session.turn_gate import (
            drain_queued_follow_up,
            host_requested_done,
            list_queued_follow_ups,
            read_turn_gate_status,
            session_pending_label,
        )

        show = self._session_is_pending()
        if show:
            try:
                drained = drain_queued_follow_up(self.session_dir)
                if drained:
                    preview = drained if len(drained) <= 48 else drained[:48] + "…"
                    self.notify(t("notify-queued-follow-up-sent", preview=preview))
            except Exception:
                pass

        meta = self.meta
        label = ""
        if show:
            try:
                label = session_pending_label(
                    self.session_dir,
                    turn_in_progress=bool(meta and meta.turn_in_progress),
                )
            except Exception:
                label = ""
            if not label and meta and meta.turn_in_progress:
                label = t("ui-turn-in-progress")

        st: dict = {}
        try:
            st = read_turn_gate_status(self.session_dir)
        except Exception:
            pass
        queued: list[str] = []
        if show:
            try:
                queued = list_queued_follow_ups(self.session_dir)
            except Exception:
                queued = []

        try:
            bar = self.query_one("#session-pending-bar")
            bar.display = show
        except Exception:
            pass
        if not show:
            try:
                self.refresh_bindings()
            except Exception:
                pass
            return

        try:
            status = self.query_one("#session-pending-status", Static)
            chip_label = label or t("browser-status-idle")
            chip = status_chip(chip_label, kind="unknown" if not label else "ok")
            sid = str(st.get("session_id") or (meta.session_id if meta else ""))
            turn = st.get("turn", "")
            bits: list[str] = []
            if sid:
                bits.append(t("ui-session-prefix", id=sid))
            if turn != "" and turn is not None:
                bits.append(t("ui-turn-number", turn=turn))
            if queued:
                bits.append(t("ui-queued-count", n=len(queued)))
            extra = ("  ·  " + "  ·  ".join(bits)) if bits else ""
            status_fp = (chip_label, extra, tuple(queued[:5]), len(queued))
            if status_fp != getattr(self, "_pending_status_fp", None):
                self._pending_status_fp = status_fp
                if not self._widget_has_text_selection(status):
                    status.update(Text.assemble(chip, Text(extra, style="dim")))
        except Exception:
            pass
        try:
            q_widget = self.query_one("#session-pending-queue", Static)
            q_fp = tuple(queued)
            if q_fp != getattr(self, "_pending_queue_fp", None):
                self._pending_queue_fp = q_fp
                if queued:
                    lines = [t("browser-follow-ups-pending", n=len(queued))]
                    for i, p in enumerate(queued[:5], start=1):
                        preview = p.replace("\n", " ")
                        if len(preview) > 72:
                            preview = preview[:69] + "…"
                        lines.append(f"  {i}. {preview}")
                    if len(queued) > 5:
                        lines.append(t("browser-more-queued", n=len(queued) - 5))
                    if not self._widget_has_text_selection(q_widget):
                        q_widget.update("\n".join(lines))
                    q_widget.display = True
                else:
                    if not self._widget_has_text_selection(q_widget):
                        q_widget.update("")
                    q_widget.display = False
        except Exception:
            pass

        # Host already requested stop: allow Done is inert; still allow viewing queue
        # but do not accept new follow-ups.
        finishing = host_requested_done(self.session_dir)
        awaiting = str(st.get("state") or "") == "awaiting_follow_up" and not finishing
        can_send = show and not finishing
        try:
            self.query_one("#session-follow-send-btn", Button).disabled = not can_send
            self.query_one("#session-follow-done-btn", Button).disabled = not show or finishing
        except Exception:
            pass
        try:
            hint = self.query_one("#session-follow-input", Input)
            if awaiting:
                hint.placeholder = U.follow_up_placeholder_awaiting()
            elif can_send:
                hint.placeholder = U.follow_up_placeholder_queue()
            hint.disabled = not can_send
        except Exception:
            pass
        try:
            self.query_one("#session-follow-last-turn", Checkbox).disabled = not can_send
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
        final = False
        with suppress(Exception):
            final = bool(self.query_one("#session-follow-last-turn", Checkbox).value)
        try:
            how = write_follow_up_for_session(self.session_dir, text, final=final)
            self.query_one("#session-follow-input", Input).value = ""
            with suppress(Exception):
                self.query_one("#session-follow-last-turn", Checkbox).value = False
            if how == "queued":
                self.notify(t("follow-up-queued-final") if final else U.follow_up_queued())
            else:
                self.notify(t("follow-up-sent-final") if final else U.follow_up_sent())
            # Session-scoped only: write_follow_up_for_session targets this
            # session's traces volume. Do not call RunManager.submit_follow_up
            # (run_id fans out to every container in a multi-model run).
        except Exception as exc:
            self.notify(U.follow_up_failed(exc), severity="error")
        self._refresh_session_pending_bar()
        self._schedule_live_refresh()

    def _session_follow_done(self) -> None:
        from ...session.turn_gate import write_done_for_session

        try:
            write_done_for_session(self.session_dir)
            rm = getattr(self.app, "run_manager", None)
            if rm is not None and hasattr(rm, "stop_session_container"):
                try:
                    rm.stop_session_container(self.session_dir)
                except Exception:
                    pass
            # Do not imply the agent finished — only that stop was requested.
            # Do not complete_interactive(run_id): that stops every sibling
            # container in a multi-session run.
            self.notify(t("mark-done-requested"))
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

    def _live_watch_root(self) -> Path:
        """Directory to watch for live refresh (session tree + turn gates).

        Turn-gate files (``.groket-turn*``) live on the container traces volume,
        typically a parent of ``session_dir``. Watching only the session dir
        misses gate-only transitions (e.g. ``awaiting_follow_up``).
        """
        from ...session.turn_gate import traces_volume_for_session

        sd = Path(self.session_dir)
        vol = traces_volume_for_session(sd)
        if vol is None:
            return sd
        try:
            if any(vol.glob(".groket-turn*")):
                return vol
            # Eval layout: ``…/<container>/<cwd-token>/<session_id>`` (uuid-like).
            if sd.name.startswith("019") and sd.parent.parent.resolve() == vol.resolve():
                return vol
        except OSError:
            pass
        return sd

    def _schedule_live_refresh(self) -> None:
        """Watch session files and arm a read-only 60s heartbeat while live.

        FS events drive timeline reloads. The heartbeat re-reads ``signals.json``
        (context meter) even when no write events fired. Neither path writes
        into the traces tree.
        """
        pending_ui = self._session_is_pending()
        live_traces = self._session_needs_live_timeline()
        if not pending_ui and not live_traces:
            self._stop_live_refresh()
            self._refresh_session_pending_bar()
            return
        self._refresh_session_pending_bar()
        from ...constants import (
            LIVE_POLL_HEARTBEAT_INTERVAL,
            LIVE_POLL_WATCH_FALLBACK_INTERVAL,
        )
        from ...fs_watch import TraceTreeWatch

        if self._trace_watch is None and self._live_refresh_timer is None:

            def _on_fs() -> None:
                try:
                    if self.app is not None and self.app.is_running:
                        self.app.call_from_thread(self._live_refresh_from_fs)
                except Exception:
                    pass

            watch = TraceTreeWatch(self._live_watch_root(), _on_fs, debounce_s=0.35)
            if watch.start():
                self._trace_watch = watch
            else:
                self._live_refresh_timer = self.set_interval(
                    LIVE_POLL_WATCH_FALLBACK_INTERVAL,
                    self._live_refresh_from_fs,
                )
        if self._live_heartbeat_timer is None:
            self._live_heartbeat_timer = self.set_interval(
                LIVE_POLL_HEARTBEAT_INTERVAL,
                self._live_refresh_heartbeat,
            )

    def _live_refresh_heartbeat(self) -> None:
        """UI thread: periodic read-only refresh (context meter / gate status)."""
        self._live_refresh_from_fs(heartbeat=True)

    def _live_refresh_from_fs(self, *, heartbeat: bool = False) -> None:
        """UI thread: react to a debounced FS event, fallback timer, or heartbeat."""
        if not self._session_is_pending() and not self._session_needs_live_timeline():
            self._stop_live_refresh()
            self._refresh_session_pending_bar()
            return
        if not self._session_needs_live_timeline() and not heartbeat:
            self._refresh_session_pending_bar()
            return
        from ...session_inflight import KIND_REFRESH, request_rerun, try_begin

        if not try_begin(KIND_REFRESH, self.session_dir):
            request_rerun(KIND_REFRESH, self.session_dir)
            self._live_refresh_pending = True
            if heartbeat:
                self._light_refresh_heartbeat = True
            return
        self._live_refresh_busy = True
        self._live_refresh_pending = False
        if heartbeat:
            self._light_refresh_heartbeat = True
        try:
            self._submit_load_data_light()
        except Exception:
            from ...session_inflight import end

            self._light_refresh_heartbeat = False
            end(KIND_REFRESH, self.session_dir)
            self._live_refresh_busy = False
            raise

    def _live_refresh_worker_done(self) -> None:
        """Release refresh inflight; run one coalesced follow-up if requested."""
        from ...session_inflight import KIND_REFRESH, end

        again = end(KIND_REFRESH, self.session_dir)
        self._live_refresh_busy = False
        self._live_refresh_pending = False
        pending_heartbeat = self._light_refresh_heartbeat
        self._light_refresh_heartbeat = False
        if again:
            self._live_refresh_from_fs(heartbeat=pending_heartbeat)

    def _submit_load_data_light(self) -> None:
        """Queue a read-only light reload on the serial live-refresh pool.

        Caller must hold the :data:`~groket.session_inflight.KIND_REFRESH` lock
        via :func:`~groket.session_inflight.try_begin`. Does not write traces.
        """
        from ...job_pools import get_live_refresh_pool

        get_live_refresh_pool().submit(
            f"refresh {self.session_dir.name}",
            self._load_data_light_job,
        )

    def _current_turn_index(self) -> int:
        try:
            from ...session.turns import segment_timeline_turns

            segs = segment_timeline_turns(self.timeline or [])
            if segs:
                return int(segs[-1].turn_index)
        except Exception:
            pass
        return 0

    def _signals_mtime(self) -> float:
        fp = Path(self.session_dir) / "signals.json"
        try:
            return float(fp.stat().st_mtime) if fp.is_file() else 0.0
        except OSError:
            return 0.0

    def _load_data_light_job(self) -> None:
        """Reload meta (+ timeline when artifacts changed). Read-only on disk.

        Always re-reads ``signals.json`` via :func:`load_session_meta` so context
        usage updates even when no write events fired (60s heartbeat). Skips
        ``parse_timeline`` when trace mtimes match to avoid multi‑MB re-reads.
        """
        from ...parser import session_trace_mtime

        try:
            mtime = session_trace_mtime(self.session_dir)
            signals_mtime = self._signals_mtime()
            timeline_unchanged = (
                self._last_trace_mtime is not None
                and mtime == self._last_trace_mtime
                and bool(self.timeline)
            )
            meta = load_session_meta(self.session_dir, include_timeline_count=False)
            if not timeline_unchanged:
                self.timeline = parse_timeline(self.session_dir)
                self._last_trace_mtime = mtime
                self._rebuild_indices()
            self.meta = meta
            if self.meta is not None:
                self.meta.num_events = len(self.timeline or [])
            self._last_signals_mtime = signals_mtime
            app = resolve_ui_app(self)
            call_ui(app, self._populate_ui_light)
        finally:
            try:
                call_ui(resolve_ui_app(self), self._live_refresh_worker_done)
            except Exception:
                from ...session_inflight import KIND_REFRESH, end

                self._light_refresh_heartbeat = False
                end(KIND_REFRESH, self.session_dir)

    def _light_refresh_fingerprint(self) -> tuple[str | int | float | bool | None, ...]:
        """Identity for live poll — skip full timeline rebuild when unchanged."""
        tl = self.timeline or []
        last = tl[-1] if tl else None
        meta = self.meta
        return (
            len(tl),
            last.index if last is not None else None,
            last.timestamp if last is not None else None,
            last.event_type if last is not None else None,
            (last.content or "")[:80] if last is not None else None,
            meta.turn_outcome if meta else None,
            meta.turn_in_progress if meta else None,
            meta.duration_seconds if meta else None,
            meta.context_usage_compact if meta else None,
            meta.context_tokens_used if meta else None,
        )

    def _reapply_timeline_view_filter(self) -> None:
        """Re-apply type / turn / search filters after a timeline reload."""
        self._apply_timeline_filters()

    def _apply_timeline_filters(self) -> None:
        """Apply View + Turn + search-as-you-type without moving focus."""
        mode = getattr(self, "_timeline_filter", "all") or "all"
        self._errors_only = mode == "errors"
        search = getattr(self, "_timeline_search", "") or ""
        if mode == "all":
            self._apply_filter(errors_only=False, search_query=search)
        elif mode == "tools":
            self._apply_filter(
                event_types=set(et.TOOL_TYPES), errors_only=False, search_query=search
            )
        elif mode == "user":
            self._apply_filter(
                event_types=set(et.USER_TYPES), errors_only=False, search_query=search
            )
        elif mode == "asst":
            self._apply_filter(
                event_types=set(et.AGENT_TYPES | et.THOUGHT_TYPES),
                errors_only=False,
                search_query=search,
            )
        elif mode == "sess":
            self._apply_filter(
                event_types=set(et.SESSION_CHROME_TYPES),
                errors_only=False,
                search_query=search,
            )
        elif mode == "errors":
            self._apply_filter(errors_only=True, search_query=search)
        else:
            self._apply_filter(errors_only=False, search_query=search)

    def _record_context_sample(self) -> bool:
        """Record read-only context snapshot against the current turn index."""
        store = getattr(self, "_context_samples", None)
        if store is None:
            return False
        return bool(store.record(self._current_turn_index(), self.meta))

    def _populate_ui_light(self) -> None:
        """Update title + timeline + share/stats without rebuilding analysis tabs.

        Skips clearing/rebuilding the timeline table when the light fingerprint
        is unchanged so live polling does not flicker mid-turn. Context-only
        changes still refresh Summary stats (read-only signals heartbeat).
        """
        sampled = self._record_context_sample()
        fp = self._light_refresh_fingerprint()
        unchanged = fp == getattr(self, "_last_light_fp", None)
        self._set_title_from_meta()
        active = ""
        with suppress(Exception):
            active = str(self.query_one("#browser-tabs", TabbedContent).active or "")
        if not unchanged:
            self._last_light_fp = fp
            try:
                timeline_table = self.query_one("#timeline-list", TimelineTable)
                timeline_table.load_events(
                    self.timeline, self._findings, list(self._flags.values())
                )
                self._rebuild_turn_select()
                self._reapply_timeline_view_filter()
            except Exception:
                pass
        if (not unchanged or sampled) and active in ("", "tab-summary", "tab-timeline"):
            if active == "tab-summary":
                try:
                    self._update_summary_tab()
                except Exception:
                    pass
                if not getattr(self, "selections", None):
                    try:
                        self._update_stats()
                    except Exception:
                        pass
        self._refresh_session_pending_bar()

    @work(thread=True)
    def _load_data(self) -> None:
        from ...parser import session_trace_mtime
        from ...session_inflight import KIND_REFRESH, end, request_rerun, try_begin

        if not try_begin(KIND_REFRESH, self.session_dir):
            # Light refresh (or another full load) owns the session; coalesce.
            request_rerun(KIND_REFRESH, self.session_dir)
            return

        try:
            self._last_light_fp = None
            self._last_trace_mtime = None
            self._last_signals_mtime = None
            store = getattr(self, "_context_samples", None)
            if store is not None:
                store.clear()
            # One timeline parse only — do not also run parse_timeline inside
            # load_session_meta (that doubled CPU on 100MB+ updates.jsonl opens).
            self.meta = load_session_meta(self.session_dir, include_timeline_count=False)
            self.timeline = parse_timeline(self.session_dir)
            if self.meta is not None:
                self.meta.num_events = len(self.timeline or [])
            try:
                self._last_trace_mtime = session_trace_mtime(self.session_dir)
            except Exception:
                self._last_trace_mtime = None
            self._last_signals_mtime = self._signals_mtime()
            self._record_context_sample()
            self._load_flags()
            self._rebuild_indices()
            try:
                self._diff_md, self._diff_meta = load_workspace_diff(self.session_dir)
            except Exception:
                self._diff_md = "# Workspace diff\n\n_Failed to load diff._\n"
                self._diff_meta = {}
            app = resolve_ui_app(self)
            call_ui(app, self._populate_ui)
            call_ui(app, self._schedule_live_refresh)
            # Analysis is async on the fixed analysis pool — never blocks timeline paint.
            call_ui(app, self._schedule_analysis)
        finally:

            def _release_refresh_lock() -> None:
                again = end(KIND_REFRESH, self.session_dir)
                self._live_refresh_busy = False
                self._live_refresh_pending = False
                pending_heartbeat = self._light_refresh_heartbeat
                self._light_refresh_heartbeat = False
                if again:
                    self._live_refresh_from_fs(heartbeat=pending_heartbeat)

            try:
                call_ui(resolve_ui_app(self), _release_refresh_lock)
            except Exception:
                self._light_refresh_heartbeat = False
                end(KIND_REFRESH, self.session_dir)

    def _should_auto_analyze(self) -> bool:
        """Whether policy says to run analyzers for this session now."""
        svc = get_analysis_service()
        when = (svc.config.auto_analyze_when or "session_complete").strip().lower()
        if when == "never":
            return False
        is_live = bool(self.meta and self.meta.turn_in_progress)
        if is_live:
            return False
        large = len(self.timeline or []) > 2_500
        if large:
            # Explicit palette analyze still works; auto skips mega timelines.
            return False
        # session_complete: settled turn outcome (not running / awaiting).
        if self.meta and (self.meta.turn_outcome or "").strip():
            oc = (self.meta.turn_outcome or "").lower().replace(" ", "_")
            if oc in ("running", "in_progress", "pending", "awaiting_follow_up"):
                return False
            return True
        return True

    def _schedule_analysis(self, *, force: bool = False) -> None:
        """Queue analysis on the serial analysis pool (UI thread)."""
        from ...analysis.inflight import (
            analysis_session_key,
            end_session_analysis,
            session_analysis_inflight,
            try_begin_session_analysis,
        )

        if self.plugin_results and not force:
            self._collect_findings()
            self._rebuild_indices()
            self._populate_analysis_ui()
            self._apply_stale_analysis_hints()
            return
        if self._analysis_pending or session_analysis_inflight(self.session_dir):
            # Already queued/running for this session — keep spinner, no second job.
            self._analysis_pending = True
            self._show_analysis_pending()
            return
        if not force and not self._should_auto_analyze():
            # Show idle findings (empty / cache only) without spinning forever.
            try:
                cached = get_analysis_service().load_cached_all(self.session_dir)
            except Exception:
                cached = {}
            if cached:
                self.plugin_results = cached
                self._collect_findings()
                self._rebuild_indices()
            else:
                self._show_analysis_idle()
                self._apply_stale_analysis_hints(repaint=False)
                return
            self._apply_stale_analysis_hints()
            return

        if not try_begin_session_analysis(self.session_dir):
            self._analysis_pending = True
            self._show_analysis_pending()
            return

        self._analysis_pending = True
        self._show_analysis_pending()
        # Clear stale banner while a fresh run is queued; show progress instead.
        self._set_analysis_stale_banner([])
        from ...job_pools import get_activity_log, get_analysis_pool
        from ..threads import call_ui

        label = self.session_dir.name
        app = self.app
        session_dir = self.session_dir
        force_run = force
        result_key = analysis_session_key(session_dir)
        legacy_key = str(session_dir)

        # Bump activity-bar counter on the UI thread so spinner shows immediately.
        try:
            app._analysis_jobs_active = (  # type: ignore[attr-defined]
                int(getattr(app, "_analysis_jobs_active", 0) or 0) + 1
            )
        except Exception:
            pass
        try:
            host_results = getattr(app, "_plugin_results", None)
            if isinstance(host_results, dict):
                host_results.pop(result_key, None)
                host_results.pop(legacy_key, None)
        except Exception:
            pass

        def _job() -> None:
            svc = get_analysis_service()
            results: dict = {}
            try:
                results = svc.analyze_all(session_dir, force=force_run)
            except Exception as exc:
                get_activity_log().log("analysis", f"failed {label}: {exc}")
                results = {}

            def _finish() -> None:
                try:
                    if self.is_mounted:
                        self.plugin_results = results
                        self._analysis_pending = False
                        self._stop_analysis_spinner_timer()
                        self._collect_findings()
                        self._rebuild_indices()
                        self._apply_stale_analysis_hints()
                    try:
                        host_results = getattr(app, "_plugin_results", None)
                        if isinstance(host_results, dict):
                            host_results[result_key] = results
                            if legacy_key != result_key:
                                host_results[legacy_key] = results
                    except Exception:
                        pass
                finally:
                    end_session_analysis(session_dir)
                    try:
                        n = int(getattr(app, "_analysis_jobs_active", 0) or 0)
                        app._analysis_jobs_active = max(0, n - 1)  # type: ignore[attr-defined]
                    except Exception:
                        pass

            try:
                call_ui(app, _finish)
            except Exception:
                end_session_analysis(session_dir)
                try:
                    n = int(getattr(app, "_analysis_jobs_active", 0) or 0)
                    app._analysis_jobs_active = max(0, n - 1)  # type: ignore[attr-defined]
                except Exception:
                    pass

        get_analysis_pool().submit(f"session {label}", _job)

    def _apply_stale_analysis_hints(self, *, repaint: bool = True) -> None:
        """Load stale hints, update banner, optionally repaint findings/report."""
        try:
            hints = get_analysis_service().stale_analyzer_hints(self.session_dir)
        except Exception:
            hints = []
        self._set_analysis_stale_banner(hints)
        if repaint and self.is_mounted and not getattr(self, "_analysis_pending", False):
            try:
                self._populate_analysis_ui()
            except Exception:
                pass

    def _stale_detail(self, hints: list[str]) -> str:
        detail = "; ".join(hints[:6])
        if len(hints) > 6:
            detail += "…"
        return detail

    def _set_analysis_stale_banner(self, hints: list[str]) -> None:
        self._analysis_stale_hints = list(hints)
        try:
            banner = self.query_one("#analysis-stale-banner", Static)
        except Exception:
            return
        if not hints:
            banner.update("")
            banner.display = False
            return
        banner.update(t("analysis-stale-banner", detail=self._stale_detail(hints)))
        banner.display = True

    def _show_analysis_idle(self) -> None:
        """Findings/report idle when auto-analyze is off or deferred."""
        try:
            findings_table = self.query_one("#findings-table", DataTable)
            findings_table.clear(columns=True)
            findings_table.add_columns("", "")
            findings_table.add_row("", t("ui-analysis-idle"))
        except Exception:
            pass
        try:
            self.query_one("#report-overview-content", Static).update(t("ui-analysis-idle-report"))
        except Exception:
            pass

    def _run_analysis(self) -> None:
        """Force analysis for this session (palette / full refresh)."""
        self.action_analyze()

    def action_analyze(self) -> None:
        """Command palette: re-run analysis for **this session only** (force)."""
        # Drop in-memory results so force actually re-runs plugins.
        self.plugin_results = {}
        self._findings = []
        self._schedule_analysis(force=True)
        self.notify(t("notify-analyzing-this-session"), severity="information", timeout=4)

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
        model = self.meta.model_display if self.meta else "unknown"
        # Full Fluent extras (not edge-space fragments). LIVE only while the agent
        # is writing traces — not for idle awaiting_follow_up or settled outcomes.
        outcome_bit = ""
        if self.meta and self.meta.turn_outcome:
            oc = (self.meta.turn_outcome or "").strip()
            oc_key = oc.lower().replace(" ", "_")
            if oc_key == "awaiting_follow_up":
                outcome_bit = t("title-browser-extra-awaiting")
            elif oc_key in ("running", "in_progress", "pending"):
                outcome_bit = t("title-browser-extra-live-turn", outcome=oc)
            else:
                outcome_bit = t("title-browser-extra-turn", outcome=oc)
        self.title = t(
            "title-browser-session",
            label=label,
            model=model,
            extra=outcome_bit or "",
        )

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
                t("notify-turn-ended-outcome", outcome=str(self.meta.turn_outcome)),
                severity="warning",
                timeout=8,
            )

    def _show_analysis_pending(self) -> None:
        """Show loading placeholders; start a cheap spinner timer (no table rebuilds)."""
        self._paint_analysis_pending_spinner(full=True)
        if self._analysis_pending:
            if self._analysis_spinner_timer is None:
                from ...constants import ANALYSIS_PENDING_SPINNER_INTERVAL

                self._analysis_spinner_timer = self.set_interval(
                    ANALYSIS_PENDING_SPINNER_INTERVAL,
                    self._tick_analysis_pending,
                )

    def _paint_analysis_pending_spinner(self, *, full: bool = False) -> None:
        """Update spinner text only (full=True also clears tables once)."""
        from ...job_pools import get_activity_log

        spin = get_activity_log().spinner_frame()
        pending_markup = t("ui-running-analysis-spinner", spin=spin)
        try:
            status = self.query_one("#findings-pending-status", Static)
            status.update(pending_markup)
            status.display = True
        except Exception:
            pass
        try:
            self.query_one("#report-overview-content", Static).update(pending_markup)
        except Exception:
            pass
        if not full:
            return
        try:
            findings_table = self.query_one("#findings-table", DataTable)
            findings_table.clear(columns=True)
            style_data_table(findings_table)
            findings_table.add_columns(
                U.col_severity(), U.col_plugin(), U.col_category(), U.col_title(), U.col_events()
            )
        except Exception:
            pass
        for aid in list(getattr(self, "_report_section_keys", ()) or ()):
            if aid == "flags":
                continue
            try:
                self.query_one(f"#{self._report_content_dom_id(aid)}", Static).update(
                    pending_markup
                )
            except Exception:
                pass

    def _tick_analysis_pending(self) -> None:
        if not self._analysis_pending or not self.is_mounted:
            self._stop_analysis_spinner_timer()
            return
        self._paint_analysis_pending_spinner(full=False)

    def _stop_analysis_spinner_timer(self) -> None:
        timer = self._analysis_spinner_timer
        self._analysis_spinner_timer = None
        if timer is not None:
            timer.stop()
        try:
            status = self.query_one("#findings-pending-status", Static)
            status.update("")
            status.display = False
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
        # First row: re-analyze needed (stale plugin cache) — not a Finding entry.
        stale_hints = getattr(self, "_analysis_stale_hints", None) or []
        if stale_hints:
            findings_table.add_row(
                "!",
                "stale",
                "",
                t("analysis-stale-findings-row", detail=self._stale_detail(stale_hints)),
                "",
                key="__analysis_stale__",
            )
        for row_idx, finding in enumerate(self._findings):
            sev_display = SEVERITY_LABEL.get(finding.severity.value, finding.severity.value)
            n_events = len(
                {
                    *finding.all_tool_call_ids,
                    *(f"u{i}" for i in finding.all_update_indices),
                    *(f"e{i}" for i in finding.all_event_indices),
                }
            )
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
            fh.append_text(status_chip(t("browser-high-chip", n=high), kind="bad"))
        elif n:
            fh.append_text(status_chip(t("browser-findings-chip", n=n), kind="unknown"))
        else:
            fh.append_text(status_chip(t("browser-status-none"), kind="ok"))
        fh.append(t("browser-findings-dim", n=n), style="dim")
        header = self.query_one("#findings-header", Static)
        if not self._widget_has_text_selection(header):
            header.update(fh)

    def _widget_has_text_selection(self, widget: object) -> bool:
        """True when the operator has a mouse/text selection on *widget*.

        Live refresh must not ``update()`` that widget or the selection vanishes
        before they can copy.
        """
        sels = getattr(self, "selections", None)
        return bool(sels and widget in sels)

    def _update_summary_tab(self) -> None:
        if not self.meta:
            return
        asst = assistant_text_from_timeline(self.timeline)
        renderable = render_session_summary(self.meta, self.timeline, assistant_text=asst)
        try:
            widget = self.query_one("#summary-content", Static)
            if self._widget_has_text_selection(widget):
                return
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
        """Export dir for finding reports (``~/.groket/reports``)."""
        from ...paths import reports_dir

        return reports_dir()

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
            opts.append((t("browser-flags-count", n=n_flags), "flags"))
        for aid in self._report_plugin_ids():
            n = sum(1 for f in self._findings if (f.plugin_id or "") == aid)
            label = self._plugin_title(aid)
            if n:
                label = f"{label} ({n})"
            else:
                label = join_ui(label, t("ui-report"))
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
            widget = self.query_one(f"#{widget_id}", Static)
            if self._widget_has_text_selection(widget):
                return
            widget.update(renderable)
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
        model = self.meta.model_display if self.meta else "unknown"
        flags = self._flags
        total = len(self._findings)
        high = sum(1 for f in self._findings if f.severity.value == "high")
        med = sum(1 for f in self._findings if f.severity.value == "medium")
        blocks: list = []
        head = Text()
        head.append(t("ui-session-report"), style="bold")
        head.append("\n")
        stale_hints = getattr(self, "_analysis_stale_hints", None) or []
        if stale_hints:
            head.append(
                t("analysis-stale-report", detail=self._stale_detail(stale_hints)),
                style="bold yellow",
            )
            head.append("\n\n")
        # Severity chips use the same Rich styles as Findings tab (not status_chip).
        if high:
            head.append(t("browser-high-chip", n=high), style=severity_style("high"))
            head.append("  ")
        if med:
            head.append(t("browser-medium-chip", n=med), style=severity_style("medium"))
            head.append("  ")
        if total and not high and not med:
            head.append(t("browser-findings-chip", n=total), style="dim")
            head.append("  ")
        if not total:
            head.append_text(status_chip(t("browser-status-clean"), kind="ok"))
            head.append("  ")
        head.append(t("browser-flags-dim", n=len(flags)), style="dim")
        head.append(" │ ", style="dim")
        head.append(
            t("browser-report-counts", total=total, high=high, med=med),
            style="dim",
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
                    t(
                        "browser-last-turn-outcome-note",
                        outcome=(self.meta.turn_outcome or "").strip(),
                    ),
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
                    err_suffix = (
                        t("browser-report-turn-err", n=seg.tool_error_count)
                        if seg.tool_error_count
                        else ""
                    )
                    span = (
                        t(
                            "browser-report-turn-span",
                            first=seg.first_index,
                            last=seg.last_index,
                        )
                        if seg.first_index is not None and seg.last_index is not None
                        else ""
                    )
                    turns_t.append_text(
                        bullet(
                            t(
                                "browser-report-turn-line",
                                label=seg.label,
                                events=seg.event_count,
                                tools=seg.tool_call_count,
                                err_suffix=err_suffix,
                                span=span,
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
            blocks.append(Text(t("browser-viewing-focus", focus=focus) + "\n", style="dim"))
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
        report_artifact = None
        if result is not None and result.ok:
            report_artifact = (result.artifacts or {}).get("report")
        # Severity-colored finding lines (same palette as Findings tab), then
        # optional full markdown report artifact (e.g. feedback).
        if plugin_findings:
            blocks.append(self._findings_report_block(plugin_findings))
        if report_artifact and str(report_artifact).strip():
            blocks.append(content_block(str(report_artifact).strip(), max_chars=12000))
        elif not plugin_findings and result is not None:
            if result.summary:
                blocks.append(Text(f"  {result.summary}\n", style="dim"))
            elif not result.ok and result.error:
                blocks.append(
                    Text(
                        t("browser-report-error", msg=result.error) + "\n",
                        style="red",
                    )
                )
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
        """Structured finding list (severity + title + detail) — not raw markdown dump.

        Severity colors use :func:`~groket.ui.styles.severity_style` — same as
        Findings tab / timeline marks (high=red, medium=dark_orange, low=yellow).
        """
        out = Text()
        for f in sorted(findings, key=lambda x: (x.severity.value, x.title or "")):
            sev_key = (f.severity.value if f.severity else "low").lower()
            sev = sev_key.upper()
            sev_style = severity_style(sev_key)
            out.append("  ")
            out.append(f"{sev:<7}", style=sev_style)
            out.append("  ")
            out.append(f.title or f.id or "(untitled)")
            cat = getattr(f, "category", None) or ""
            if cat:
                out.append(f"  ·  {cat}", style="dim")
            n_ev = len(
                {
                    *(getattr(f, "all_tool_call_ids", None) or []),
                    *(f"u{i}" for i in (getattr(f, "all_update_indices", None) or [])),
                    *(f"e{i}" for i in (getattr(f, "all_event_indices", None) or [])),
                }
            )
            if n_ev:
                out.append("  ·  " + t("browser-finding-events", n=n_ev), style="dim")
            out.append("\n")
            detail = (getattr(f, "detail", None) or "").strip()
            if detail:
                # Preserve structure for multi-line reviews; only collapse tiny blurbs.
                if "\n" in detail or len(detail) > 280:
                    for i, dl in enumerate(detail.splitlines()[:24]):
                        out.append(f"           {dl}\n", style="dim")
                    if detail.count("\n") >= 24:
                        out.append("           …\n", style="dim")
                else:
                    one_line = " ".join(detail.split())
                    if len(one_line) > 220:
                        one_line = one_line[:217] + "…"
                    out.append(f"           {one_line}\n", style="dim")
            children = getattr(f, "children", None) or []
            for ch in children[:8]:
                ch_title = getattr(ch, "title", None) or getattr(ch, "id", "") or ""
                out.append(f"           - {ch_title}\n", style="dim")
            if len(children) > 8:
                out.append(
                    t("browser-more-children", n=len(children) - 8),
                    style="dim",
                )
        if not findings:
            out.append(t("ui-none"), style="dim")
        return out

    def _update_stats(self) -> None:
        """Fill Summary-pane tables (turns, event mix, tool timing, phases)."""
        if not self.meta:
            return
        m = self.meta
        type_counts = Counter(e.event_type for e in self.timeline)
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
        try:
            from ...session.turns import segment_timeline_turns, turn_summary_rows

            turn_segments = segment_timeline_turns(self.timeline)
            samples = {}
            store = getattr(self, "_context_samples", None)
            if store is not None:
                samples = store.compact_by_turn()
            turn_rows = turn_summary_rows(
                turn_segments,
                durations=durations,
                session_context_compact=m.context_usage_compact,
                context_by_turn=samples,
            )
        except Exception:
            turn_rows = []
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
                t("ui-context"),
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
                    ctx = str(row.get("context") or "").strip() or "—"
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
                        ctx[:28],
                        str(row.get("top_tools", "—"))[:40],
                        span,
                        key=f"turn-{row.get('turn')}",
                    )
            else:
                turns_table.add_row(
                    "—",
                    t("ui-no-timeline"),
                    "—",
                    "0",
                    "0",
                    "—",
                    "0",
                    "0",
                    "—",
                    "—",
                    "—",
                    "—",
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
        try:
            from ...session.usage_stats import collect_session_usage

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
            "agent_thought_chunk": t("ui-thinking"),
            "agent_message_chunk": t("ui-writing"),
            "tool_call": t("ui-tool-execution"),
            "tool_call_update": t("ui-tool-execution"),
            "user_message_chunk": t("ui-user-input"),
            "plan": t("ui-planning"),
            "subagent_spawned": t("ui-subagent"),
            "subagent_finished": t("ui-subagent"),
            "task_backgrounded": t("ui-subagent"),
            "task_completed": t("ui-subagent"),
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
                self.activate_tab_pane("tab-timeline")
        except Exception:
            self.activate_tab_pane("tab-timeline")

    def _apply_timeline_mode(self, mode: str) -> None:
        """Apply View dropdown mode; keyboard and Select stay aligned."""
        if mode not in self._TIMELINE_VIEWS:
            mode = "all"
        self._ensure_timeline_tab()
        self._timeline_filter = mode
        self._sync_timeline_view_select(mode)
        self._apply_timeline_filters()

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
        self._apply_timeline_filters()

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
        event_indices = set(finding.all_event_indices)
        timeline_table = self.query_one("#timeline-list", TimelineTable)
        timeline_table.apply_filter(
            call_ids=call_ids or None,
            update_indices=update_indices or None,
            event_indices=event_indices or None,
        )
        tabbed = self.query_one(TabbedContent)
        tabbed.active = "tab-timeline"

    @on(Input.Changed, "#search-input")
    def _on_search_changed(self, event: Input.Changed) -> None:
        """Filter timeline as you type (Textual ``Input.Changed`` — no Enter)."""
        self._timeline_search = event.value or ""
        self._apply_timeline_filters()

    @on(Input.Submitted, "#search-input")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        """Enter keeps the filter and moves focus to the timeline list."""
        self._timeline_search = event.value or ""
        self._apply_timeline_filters()
        try:
            focus_primary_list(self.query_one("#timeline-list", TimelineTable))
        except Exception:
            pass

    def _turn_event_indices(self) -> set[int] | None:
        """Event indices for the Turn filter, or None for all turns.

        Session-level timeline rows (e.g. system prompt) are not part of any
        turn segment but stay visible when a specific turn is selected.
        """
        tf = getattr(self, "_turn_filter", "all")
        if tf in (None, "", "all"):
            return None
        try:
            ti = int(tf)
        except (TypeError, ValueError):
            return None
        from ...session.turns import is_session_level_timeline_event

        for seg in getattr(self, "_turn_segments", None) or []:
            if seg.turn_index == ti:
                indices = {e.index for e in seg.events}
                for ev in self.timeline:
                    if is_session_level_timeline_event(ev):
                        indices.add(ev.index)
                return indices
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
        if "search_query" not in kwargs:
            kwargs["search_query"] = getattr(self, "_timeline_search", "") or ""
        timeline_table.apply_filter(**kwargs)

    @on(Select.Changed, "#timeline-turn-select")
    def _on_timeline_turn_changed(self, event: Select.Changed) -> None:
        val = event.value
        if val is Select.BLANK or val is None:
            return
        self._turn_filter = str(val)
        self._apply_timeline_filters()

    def action_search(self) -> None:
        self._ensure_timeline_tab()

        def _focus_search() -> None:
            try:
                self.query_one("#search-input", Input).focus()
            except Exception:
                pass

        self.call_after_refresh(lambda: self.call_after_refresh(_focus_search))

    def action_clear_filters(self) -> None:
        self._timeline_search = ""
        try:
            self.query_one("#search-input", Input).value = ""
        except Exception:
            pass
        self._apply_timeline_mode("all")

    def action_show_findings(self) -> None:
        """Jump to Findings (same as tab 4 / ``i``)."""
        self.activate_tab_pane("tab-findings")

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

    def check_action(
        self,
        action: str,
        parameters: tuple[object, ...],  # Textual Screen.check_action
    ) -> bool | None:
        """Hide Flag in the footer/bindings unless a timeline event is selected+focused."""
        if action == "flag_event":
            return True if self._timeline_event_actionable() else False
        if action in ("send_follow_up", "mark_session_done", "focus_follow_up"):
            return self._session_is_pending()
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
        model = self.meta.model_display if self.meta else "unknown"
        session_id = self.meta.session_id if self.meta else "unknown"
        lines = [
            t("report-md-model", model=model),
            t("report-md-session", id=session_id),
            t("report-md-plugin", id=finding.plugin_id),
            t("report-md-finding", id=finding.id),
            t("report-md-severity", sev=finding.severity.value.upper()),
            t("report-md-category", cat=finding.category),
            "",
            f"**{finding.title}**",
        ]
        if finding.detail:
            lines.append("")
            for dl in finding.detail.strip().splitlines():
                lines.append(f"> {dl}")
        if finding.children:
            lines.append("")
            lines.append(t("report-md-sub-findings", n=len(finding.children)))
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

    def action_delete_session(self) -> None:
        """Double-press ``x`` deletes this session from disk and leaves the browser."""
        from ..delete_confirm import second_press_armed

        key = str(self.session_dir)
        pending = [key] if self._delete_pending else []
        commit, _pending = second_press_armed(pending, [key])
        if not commit:
            self._delete_pending = True
            self.notify(
                t("notify-delete-session-arm"),
                severity="warning",
                timeout=10,
            )
            return
        self._delete_pending = False
        self._stop_live_refresh()
        from ...runs.run_configs import delete_session_dirs, session_dirs_for_delete

        paths = session_dirs_for_delete([self.session_dir])
        traces_root = getattr(self.app, "traces_path", None)
        stats = delete_session_dirs(paths, traces_root=traces_root, prune_empty_parents=True)
        gone = {str(p) for p in paths}
        app = self.app
        # Drop from home-screen caches while we still hold the app ref.
        meta_only = getattr(app, "_meta_only", None)
        if isinstance(meta_only, list):
            setattr(
                app,
                "_meta_only",
                [(m, lab) for m, lab in meta_only if str(m.session_dir) not in gone],
            )
        selected = getattr(app, "_selected", None)
        if isinstance(selected, set):
            selected -= gone
        plugin_results = getattr(app, "_plugin_results", None)
        if isinstance(plugin_results, dict):
            for k in list(plugin_results):
                if k in gone:
                    del plugin_results[k]
        err_n = 0
        errors_raw = stats.get("errors")
        if isinstance(errors_raw, list):
            err_n = len(errors_raw)
        err_suffix = t("notify-deleted-sessions-errors", n=err_n) if err_n else ""
        self.notify(
            t(
                "notify-deleted-sessions",
                deleted=stats.get("deleted", 0),
                requested=stats.get("requested", 0),
                err_suffix=err_suffix,
            ),
            severity="warning" if err_n else "information",
            timeout=10,
        )
        self.app.pop_screen()
        populate = getattr(app, "_populate_session_table", None)
        if callable(populate):
            with suppress(Exception):
                populate()

    def action_export_finding(self) -> None:
        """Export the selected finding to a markdown file (command palette)."""
        tabbed = self.query_one(TabbedContent)
        if tabbed.active != "tab-findings" or self._selected_finding is None:
            self.notify(U.select_finding_first(), severity="warning")
            return
        self._report_finding(self._selected_finding)
