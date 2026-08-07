"""Timeline widget showing trace events in a scrollable list."""

from __future__ import annotations

from rich.markup import escape as rich_escape
from textual.message import Message
from textual.widgets import DataTable

from ... import event_types as et
from ...analysis.base import Finding
from ...constants import LIVE_TIMELINE_TAIL_CHECK
from ...models import Flag, TraceEvent
from ...utils import fmt_duration
from ..data_table import preserving_cursor, style_data_table, update_row_cell
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
    #: event.index → sequential operator turn id (0-based); empty when unknown
    _turn_by_index: dict[int, int] = {}

    @property
    def durations(self) -> dict[int, float]:
        """Computed per-event durations (event index -> seconds)."""
        return self._durations

    def on_mount(self) -> None:
        style_data_table(self)
        self.add_columns(
            "#",
            t("col-turn"),
            t("col-time"),
            t("col-dur"),
            t("col-type"),
            t("col-tool"),
            t("col-summary"),
        )

    def load_events(
        self,
        events: list[TraceEvent],
        findings: list[Finding] | None = None,
        flags: list[Flag] | None = None,
    ) -> None:
        """Load timeline rows.

        Live multi-turn refresh paths (cheapest first):

        1. **Skip** — same length, same visible cells.
        2. **Append + optional tail patch** — previous rows are a structural
           prefix of *events*; patch any streamed content on the overlap, then
           append new rows (no ``clear()``).
        3. **Same-length tail patch** — structure unchanged, only trailing
           summaries/durations changed (streaming assistant text).
        4. **Full rebuild** — order/identity changed (filters, re-sort).

        Live ticks only re-check the last :data:`LIVE_TIMELINE_TAIL_CHECK` rows
        for visual equality so streaming does not walk hundreds of ``summary_line``
        properties on the UI thread.
        """
        prev = self.events
        new_events = events or []
        prev_n = len(prev)
        new_n = len(new_events)
        row_ok = self.row_count == prev_n and prev_n > 0

        self.events = new_events
        self.findings_by_call = {}
        if findings:
            for f in findings:
                for cid in f.all_tool_call_ids:
                    self.findings_by_call[cid] = f
        self.flags_by_index = {}
        if flags:
            for fl in flags:
                self.flags_by_index[fl.event_index] = fl
        self._rebuild_turn_map()

        if not row_ok or not prev:
            self._build_tool_pairs()
            self._compute_durations()
            self._refresh_rows()
            return

        # Live growth / stream: only the tail can change. O(tail) not O(n).
        if new_n >= prev_n and self._live_tail_struct_ok(prev, new_events):
            # Append-only: ignore content-only rewrites of existing rows (agent
            # streaming). Patching every token froze the TUI; new tool rows still
            # appear when len grows. Full paint happens on F5 / open.
            if new_n == prev_n:
                return
            self._index_new_events(new_events[prev_n:])
            self._append_rows(new_events[prev_n:])
            self._patch_paired_call_durations(new_events[prev_n:])
            return

        # Structural mid-list change (filters / re-sort): full rebuild.
        self._build_tool_pairs()
        self._compute_durations()
        self._refresh_rows()

    @staticmethod
    def _live_tail_struct_ok(prev: list[TraceEvent], new: list[TraceEvent]) -> bool:
        """True when the live tail of *prev* is a structural prefix of *new*."""
        prev_n = len(prev)
        if len(new) < prev_n or prev_n == 0:
            return False
        tail = min(LIVE_TIMELINE_TAIL_CHECK, prev_n)
        start = prev_n - tail
        for i in range(start, prev_n):
            a, b = prev[i], new[i]
            if a.index != b.index or a.event_type != b.event_type:
                return False
            if a.tool_call_id != b.tool_call_id:
                return False
        return True

    @staticmethod
    def _live_tail_visual_from(
        prev: list[TraceEvent], new: list[TraceEvent], prev_n: int
    ) -> int | None:
        """First visually different index in the live tail, or None if tail matches."""
        tail = min(LIVE_TIMELINE_TAIL_CHECK, prev_n)
        start = prev_n - tail
        first: int | None = None
        for i in range(start, prev_n):
            if not TimelineTable._row_visually_equal(prev[i], new[i]):
                if first is None:
                    first = i
        return first

    @staticmethod
    def _structural_prefix_len(prev: list[TraceEvent], new: list[TraceEvent]) -> int:
        """How many leading events share index/type/tool_call_id."""
        n = 0
        for a, b in zip(prev, new, strict=False):
            if a.index != b.index or a.event_type != b.event_type:
                break
            if a.tool_call_id != b.tool_call_id:
                break
            n += 1
        return n

    @staticmethod
    def _prefix_matches(prev: list[TraceEvent], new: list[TraceEvent]) -> bool:
        """True when *prev* is the leading segment of *new* (live growth)."""
        if len(new) < len(prev):
            return False
        return TimelineTable._structural_prefix_len(prev, new) == len(prev)

    @staticmethod
    def _row_visually_equal(a: TraceEvent, b: TraceEvent) -> bool:
        """True when one row's visible cells would match."""
        return (
            a.index == b.index
            and a.event_type == b.event_type
            and a.tool_call_id == b.tool_call_id
            and a.is_error == b.is_error
            and a.tool_name == b.tool_name
            and a.summary_line == b.summary_line
            and a.time_str == b.time_str
        )

    @staticmethod
    def _first_visual_mismatch(
        prev: list[TraceEvent], new: list[TraceEvent], overlap: int
    ) -> int | None:
        """Index of first visually different row in ``[0, overlap)``, or None."""
        for i in range(overlap):
            if not TimelineTable._row_visually_equal(prev[i], new[i]):
                return i
        return None

    @staticmethod
    def _rows_visually_equal(prev: list[TraceEvent], new: list[TraceEvent]) -> bool:
        """True when summary cells would not change (skip DataTable rebuild)."""
        if len(prev) != len(new):
            return False
        return TimelineTable._first_visual_mismatch(prev, new, len(prev)) is None

    def _append_rows(self, new_events: list[TraceEvent]) -> None:
        """Add only *new_events* (already assigned into ``self.events``)."""
        for ev in new_events:
            self._add_event_row(ev)

    def _patch_rows(self, events: list[TraceEvent]) -> None:
        """Rewrite cells for existing rows (streaming content) without clear()."""
        for ev in events:
            self._update_event_row(ev)

    def _patch_paired_call_durations(self, events: list[TraceEvent]) -> None:
        """When a tool_result updates, refresh the earlier tool_call duration cell."""
        seen: set[int] = set()
        for ev in events:
            if ev.event_type not in et.TOOL_UPDATE_TYPES or not ev.tool_call_id:
                continue
            call = self._call_by_id.get(ev.tool_call_id)
            if call is None or call.index in seen:
                continue
            seen.add(call.index)
            self._update_event_row(call)

    def _index_new_events(self, new_events: list[TraceEvent]) -> None:
        """Update tool pairs + durations for appended/patched events (live path)."""
        for ev in new_events:
            if not ev.tool_call_id:
                continue
            if ev.event_type == "tool_call":
                self._call_by_id[ev.tool_call_id] = ev
            elif ev.event_type in et.TOOL_UPDATE_TYPES:
                self._result_by_id[ev.tool_call_id] = ev
        # Durations: tool_call ↔ result, or next-event gap for new non-tool rows.
        for ev in new_events:
            if ev.timestamp is None:
                continue
            if ev.event_type == "tool_call" and ev.tool_call_id in self._result_by_id:
                res = self._result_by_id[ev.tool_call_id]
                if res.timestamp is not None and res.timestamp >= ev.timestamp:
                    self._durations[ev.index] = res.timestamp - ev.timestamp
            elif ev.event_type in et.TOOL_UPDATE_TYPES and ev.tool_call_id:
                call = self._call_by_id.get(ev.tool_call_id)
                if (
                    call is not None
                    and call.timestamp is not None
                    and ev.timestamp >= call.timestamp
                ):
                    self._durations[call.index] = ev.timestamp - call.timestamp

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

    def _rebuild_turn_map(self) -> None:
        """Map each loaded event index to its sequential operator turn id."""
        from ...session.turns import event_display_turn_map, segment_timeline_turns

        if not self.events:
            self._turn_by_index = {}
            return
        self._turn_by_index = event_display_turn_map(segment_timeline_turns(self.events))

    def _turn_cell(self, ev: TraceEvent) -> str:
        """Compact turn id for the Turn column (or dim dash when session-level)."""
        ti = self._turn_by_index.get(int(ev.index))
        if ti is None:
            return "[dim]—[/]"
        return f"[cyan]{ti}[/]"

    def _row_cell_values(self, ev: TraceEvent) -> tuple[str, str, str, str, str, str, str]:
        """Visible cell values for one event (columns 0–6)."""
        from ...session.turns import harness_user_chrome_heading

        chrome_heading = harness_user_chrome_heading(ev.content or "")
        if chrome_heading is not None:
            # Harness injects system-reminder / background-task as user_message_chunk.
            type_style = f"[bold magenta]{chrome_heading.lower()}[/]"
        else:
            type_style = TYPE_MARKUP.get(ev.event_type, ev.event_type.upper())
        tool_err = ev.is_error and ev.event_type not in et.SESSION_CHROME_TYPES
        if tool_err and chrome_heading is None:
            type_style = f"[red bold underline]{ev.type_label}[/]"
        elif ev.event_type in et.ERROR_TYPES and chrome_heading is None:
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
        return (
            str(ev.index),
            self._turn_cell(ev),
            ev.time_str,
            dur_str,
            type_style,
            tool_col,
            summary,
        )

    def _add_event_row(self, ev: TraceEvent) -> None:
        """Append one timeline row for *ev*."""
        cells = self._row_cell_values(ev)
        self.add_row(*cells, key=str(ev.index))

    def _update_event_row(self, ev: TraceEvent) -> None:
        """Patch an existing row's cells in place (streaming live refresh)."""
        key = str(ev.index)
        cells = self._row_cell_values(ev)
        for col_i, value in enumerate(cells):
            update_row_cell(self, key, col_i, value)

    def _refresh_rows(self) -> None:
        with preserving_cursor(self, scroll=False):
            self.clear()
            for ev in self.events:
                self._add_event_row(ev)

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
