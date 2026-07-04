"""Timeline widget showing trace events in a scrollable list."""

from __future__ import annotations

from rich.markup import escape as rich_escape
from textual.message import Message
from textual.widgets import DataTable

from ... import event_types as et
from ...analysis.base import Finding
from ...models import Flag, TraceEvent
from ...utils import fmt_duration
from ..data_table import preserving_cursor, style_data_table
from ..i18n import t
from ..styles import EVENT_TYPE_LABEL as TYPE_MARKUP
from ..styles import finding_mark
from ..styles import tool_label as tool_markup


class TimelineTable(DataTable):
    """DataTable specialized for trace event timelines."""

    class EventSelected(Message):
        def __init__(self, event: TraceEvent) -> None:
            super().__init__()
            self.event = event

    events: list[TraceEvent] = []
    findings_by_call: dict[str, Finding] = {}
    flags_by_index: dict[int, Flag] = {}
    _durations: dict[int, float] = {}
    _call_by_id: dict[str, TraceEvent] = {}
    _result_by_id: dict[str, TraceEvent] = {}

    @property
    def durations(self) -> dict[int, float]:
        """Computed per-event durations (event index -> seconds)."""
        return self._durations

    def on_mount(self) -> None:
        style_data_table(self)
        self.add_columns(
            "#", t("col-time"), t("col-dur"), t("col-type"), t("col-tool"), t("col-summary")
        )

    def load_events(
        self,
        events: list[TraceEvent],
        findings: list[Finding] | None = None,
        flags: list[Flag] | None = None,
    ) -> None:
        self.events = events
        self.findings_by_call = {}
        if findings:
            for f in findings:
                for cid in f.all_tool_call_ids:
                    self.findings_by_call[cid] = f
        self.flags_by_index = {}
        if flags:
            for fl in flags:
                self.flags_by_index[fl.event_index] = fl
        self._build_tool_pairs()
        self._compute_durations()
        self._refresh_rows()

    def _build_tool_pairs(self) -> None:
        """Index tool_call / tool_result by call_id (trace_viewer merges these)."""
        self._call_by_id = {}
        self._result_by_id = {}
        for ev in self.events:
            if not ev.tool_call_id:
                continue
            if ev.event_type == "tool_call":
                self._call_by_id[ev.tool_call_id] = ev
            elif ev.event_type in et.TOOL_UPDATE_TYPES:
                self._result_by_id[ev.tool_call_id] = ev

    def get_paired_call(self, ev: TraceEvent) -> TraceEvent | None:
        if ev.event_type in et.TOOL_UPDATE_TYPES and ev.tool_call_id:
            return self._call_by_id.get(ev.tool_call_id)
        return None

    def get_paired_result(self, ev: TraceEvent) -> TraceEvent | None:
        if ev.event_type == "tool_call" and ev.tool_call_id:
            return self._result_by_id.get(ev.tool_call_id)
        return None

    def _compute_durations(self) -> None:
        """Compute per-event durations from timestamps.

        For tool_call events, duration = time until the matching tool_result.
        For other events, duration = time until the next event.
        """
        self._durations = {}
        if not self.events:
            return
        result_ts: dict[str, int] = {}
        for ev in self.events:
            if ev.event_type in et.TOOL_UPDATE_TYPES and ev.tool_call_id and ev.timestamp:
                result_ts[ev.tool_call_id] = ev.timestamp
        for i, ev in enumerate(self.events):
            if ev.timestamp is None:
                continue
            if ev.event_type == "tool_call" and ev.tool_call_id in result_ts:
                dur = result_ts[ev.tool_call_id] - ev.timestamp
                if dur >= 0:
                    self._durations[ev.index] = dur
            elif ev.event_type in et.TOOL_UPDATE_TYPES:
                continue
            else:
                ev_ts = ev.timestamp
                for j in range(i + 1, len(self.events)):
                    next_ts = self.events[j].timestamp
                    if next_ts is not None and ev_ts is not None:
                        dur = next_ts - ev_ts
                        if dur >= 0:
                            self._durations[ev.index] = dur
                        break

    @staticmethod
    def _fmt_dur(seconds: float) -> str:
        return fmt_duration(seconds)

    def _tool_column(self, ev: TraceEvent) -> str:
        """Tool / runtime label — same family palette as ``tool_label`` (not per-tool rainbow)."""
        if ev.event_type in et.TOOL_TYPES and ev.tool_name:
            return tool_markup(ev.tool_name)
        if ev.event_type in et.ERROR_TYPES:
            return t("ui-session-error-1")
        if ev.event_type in et.TURN_BOUNDARY_TYPES:
            label = ev.type_label
            c = (ev.content or "").lower()
            if t("ui-turn-ended") in c:
                label = t("ui-turn-ended")
            elif t("ui-turn-started") in c:
                label = t("ui-turn-started")
            return f"[yellow]{label}[/]"
        if ev.event_type in et.SUBAGENT_TYPES:
            return "[cyan]subagent[/]"
        if ev.event_type in (et.MESSAGE_TYPES | et.PLAN_TYPES):
            return ""
        return ""

    def _refresh_rows(self) -> None:
        with preserving_cursor(self, scroll=False):
            self.clear()
            for ev in self.events:
                type_style = TYPE_MARKUP.get(ev.event_type, ev.event_type.upper())
                tool_err = ev.is_error and ev.event_type not in et.SESSION_CHROME_TYPES
                if tool_err:
                    type_style = f"[red bold underline]{ev.type_label}[/]"
                elif ev.event_type in et.ERROR_TYPES:
                    type_style = f"[red bold underline]{ev.type_label}[/]"
                dur_str = ""
                if ev.index in self._durations:
                    dur = self._durations[ev.index]
                    dur_str = self._fmt_dur(dur)
                    if dur >= 60:
                        dur_str = f"[red bold]{dur_str}[/]"
                    elif dur >= 30:
                        dur_str = f"[yellow]{dur_str}[/]"
                tool_col = self._tool_column(ev)
                if tool_err and tool_col and (not tool_col.startswith("[")):
                    tool_col = f"[red]{tool_col}[/]"
                prefix = ""
                if ev.index in self.flags_by_index:
                    prefix += "[magenta bold]⚑[/] "
                if ev.tool_call_id and ev.tool_call_id in self.findings_by_call:
                    finding = self.findings_by_call[ev.tool_call_id]
                    sev = getattr(finding.severity, "value", None) or "low"
                    prefix += finding_mark(sev) + " "
                summary = prefix + rich_escape(ev.summary_line[: 56 if prefix else 60])
                self.add_row(
                    str(ev.index),
                    ev.time_str,
                    dur_str,
                    type_style,
                    tool_col,
                    summary,
                    key=str(ev.index),
                )

    def apply_filter(
        self,
        event_type: str | None = None,
        event_types: set[str] | None = None,
        tool_name: str | None = None,
        errors_only: bool = False,
        flagged_only: bool = False,
        search_query: str = "",
        call_ids: set[str] | None = None,
        update_indices: set[int] | None = None,
        event_indices: set[int] | None = None,
    ) -> None:
        """Re-filter the displayed events."""
        filtered = self.events
        if event_type:
            filtered = [e for e in filtered if e.event_type == event_type]
        if event_types:
            filtered = [e for e in filtered if e.event_type in event_types]
        if tool_name:
            filtered = [e for e in filtered if e.tool_name == tool_name]
        if errors_only:
            filtered = [e for e in filtered if e.is_error or e.event_type in et.ERROR_TYPES]
        if flagged_only:
            filtered = [e for e in filtered if e.index in self.flags_by_index]
        if search_query:
            q = search_query.lower()
            filtered = [
                e
                for e in filtered
                if q in e.content.lower()
                or q in e.tool_name.lower()
                or q in str(e.raw_input).lower()
            ]
        # Evidence links: OR across tool_call_id, update_index, and event index.
        if call_ids is not None or update_indices is not None or event_indices is not None:
            ids = call_ids or set()
            upds = update_indices or set()
            eidxs = event_indices or set()
            if ids or upds or eidxs:

                def _evidence_match(e: TraceEvent) -> bool:
                    if ids and e.tool_call_id in ids:
                        return True
                    if upds and e.update_index in upds:
                        return True
                    if eidxs and e.index in eidxs:
                        return True
                    return False

                filtered = [e for e in filtered if _evidence_match(e)]
        orig = self.events
        self.events = filtered
        self._refresh_rows()
        self.events = orig

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row_key = event.row_key
        if row_key is None:
            return
        raw = str(row_key.value).strip()
        if not raw.isdigit():
            return
        idx = int(raw)
        matching = [e for e in self.events if e.index == idx]
        if matching:
            self.post_message(self.EventSelected(matching[0]))
