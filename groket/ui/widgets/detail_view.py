"""Detail view widget for displaying event details (Rich/Markdown/Syntax)."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import DataTable, Static

from ...models import TraceEvent
from ...session.jobs import ScheduleTask
from ...session.subagents import SubagentRun
from ...session.workflows import WorkflowChild, WorkflowRun
from ..data_table import ListDataTable, style_data_table
from ..i18n import t
from ..render_detail import (
    DetailSection,
    event_detail_sections,
    render_event_detail,
    render_workflow_detail,
    set_static_renderable,
    workflow_detail_sections,
)
from ..selectable_static import SelectableStatic, plain_from_renderable

_SECTION_SIDS = (
    "chrome",
    "input",
    "output",
    "asked",
    "happened",
    "failed",
    "log",
    "thought",
    "plan",
    "message",
    "subagent",
    "session",
    "body",
)


class DetailView(VerticalScroll):
    """Shows detailed information about a selected trace event.

    Mounts one panel card per event section (Input, Output, Asked, …)
    so each body is extractable. SelectableStatic enables mouse
    text selection and plain-text clipboard yank for Markdown/Syntax bodies.
    """

    class ChildActivated(Message):
        """Operator activated a workflow child row."""

        def __init__(self, child: WorkflowChild) -> None:
            super().__init__()
            self.child = child

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._current_event: TraceEvent | None = None
        self._current_duration: float | None = None
        self._paired_call: TraceEvent | None = None
        self._paired_result: TraceEvent | None = None
        self._current_turn_index: int | None = None
        self._subagent_run: SubagentRun | None = None
        self._job_mate: TraceEvent | None = None
        self._schedule: ScheduleTask | None = None
        self._workflow: WorkflowRun | None = None
        self.session_dir: Path | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-sections"):
            for sid in _SECTION_SIDS:
                body_id = self._section_body_id(sid)
                with Vertical(id=f"detail-sec-{sid}", classes="panel-card"):
                    yield Static("", classes="panel-card-title", id=f"detail-title-{sid}")
                    yield SelectableStatic("", id=body_id, classes="detail-section-body")
        yield ListDataTable(id="workflow-children-table")

    def show_event(
        self,
        event: TraceEvent,
        duration: float | None = None,
        *,
        paired_call: TraceEvent | None = None,
        paired_result: TraceEvent | None = None,
        turn_index: int | None = None,
        subagent_run: SubagentRun | None = None,
        job_mate: TraceEvent | None = None,
        schedule: ScheduleTask | None = None,
        workflow: WorkflowRun | None = None,
    ) -> None:
        same_event = self._current_event is not None and int(self._current_event.index) == int(
            event.index
        )
        self._current_event = event
        self._current_duration = duration
        self._paired_call = paired_call
        self._paired_result = paired_result
        self._current_turn_index = turn_index
        self._subagent_run = subagent_run
        self._job_mate = job_mate
        self._schedule = schedule
        self._workflow = workflow
        self._refresh_content(scroll_home=not same_event)
        self._sync_workflow_children()

    def show_workflow(self, run: WorkflowRun) -> None:
        """Inspect a Summary row when no Timeline bookend can be paired."""
        self._current_event = None
        self._current_duration = None
        self._paired_call = None
        self._paired_result = None
        self._current_turn_index = None
        self._subagent_run = None
        self._job_mate = None
        self._schedule = None
        self._workflow = run
        self._sync_detail_sections(workflow_detail_sections(run))
        self._sync_workflow_children()
        self.scroll_home(animate=False)

    def _section_body_id(self, sid: str) -> str:
        return "detail-body" if sid == "chrome" else f"detail-body-{sid}"

    def _sync_detail_sections(self, sections: list[DetailSection]) -> None:
        by_sid = {sec.sid: sec for sec in sections}
        for sid in _SECTION_SIDS:
            try:
                card = self.query_one(f"#detail-sec-{sid}", Vertical)
                title = self.query_one(f"#detail-title-{sid}", Static)
                body = self.query_one(f"#{self._section_body_id(sid)}", SelectableStatic)
            except Exception:
                continue
            sec = by_sid.get(sid)
            if sec is None:
                card.display = False
                continue
            card.display = True
            if sec.title:
                title.update(sec.title)
                title.display = True
            else:
                title.update("")
                title.display = False
            if not self._body_has_selection(body):
                set_static_renderable(body, sec.body)

    def _body_has_selection(self, widget: object) -> bool:
        sels = getattr(self.screen, "selections", None)
        return bool(sels and widget in sels)

    def has_text_selection(self) -> bool:
        """True when any section body has an active drag selection."""
        for widget in self.query(SelectableStatic):
            if self._body_has_selection(widget):
                return True
        return False

    def visible_plain(self) -> str:
        """Joined plain text of every visible section body."""
        bits: list[str] = []
        for sid in _SECTION_SIDS:
            try:
                card = self.query_one(f"#detail-sec-{sid}", Vertical)
                if not card.display:
                    continue
                body = self.query_one(f"#{self._section_body_id(sid)}", SelectableStatic)
            except Exception:
                continue
            bits.append(body.get_plain_text() or "")
        return "\n".join(bits)

    def _refresh_content(self, *, scroll_home: bool = True) -> None:
        ev = self._current_event
        if ev is None:
            if self._workflow is not None:
                self._sync_detail_sections(workflow_detail_sections(self._workflow))
                self._sync_workflow_children()
                return
            self._sync_detail_sections([])
            self._sync_workflow_children()
            return
        self._sync_detail_sections(
            event_detail_sections(
                ev,
                duration=self._current_duration,
                paired_call=self._paired_call,
                paired_result=self._paired_result,
                turn_index=self._current_turn_index,
                subagent_run=self._subagent_run,
                job_mate=self._job_mate,
                schedule=self._schedule,
                workflow=self._workflow,
                session_dir=self.session_dir,
            )
        )
        self._sync_workflow_children()
        if scroll_home:
            self.scroll_home(animate=False)

    def on_mount(self) -> None:
        for sid in _SECTION_SIDS:
            try:
                self.query_one(f"#detail-sec-{sid}", Vertical).display = False
            except Exception:
                pass
        table = self.query_one("#workflow-children-table", DataTable)
        style_data_table(table)
        table.add_columns(t("ui-agents"), t("col-status"))
        table.display = False

    def _sync_workflow_children(self) -> None:
        try:
            table = self.query_one("#workflow-children-table", DataTable)
        except Exception:
            return
        run = self._workflow
        kids = list(run.children) if run is not None else []
        table.clear()
        if not kids:
            table.display = False
            return
        table.display = True
        parent = self.session_dir
        for i, child in enumerate(kids):
            openable = child.session_path(parent) is not None
            label = child.label or child.agent_id
            mark = t("status-complete") if child.success else t("ui-status-failed")
            if openable:
                table.add_row(label, mark, key=f"wfchild-{i}")
            else:
                table.add_row(
                    Text(label, style="dim"),
                    Text(mark, style="dim"),
                    key=f"wfchild-{i}",
                )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "workflow-children-table":
            return
        raw = str(event.row_key.value) if event.row_key is not None else ""
        if not raw.startswith("wfchild-"):
            return
        try:
            idx = int(raw.split("-", 1)[1])
        except ValueError:
            return
        run = self._workflow
        if run is None or not (0 <= idx < len(run.children)):
            return
        child = run.children[idx]
        if child.session_path(self.session_dir) is None:
            return
        self.post_message(self.ChildActivated(child))

    def clear_detail(self) -> None:
        self._current_event = None
        self._current_duration = None
        self._paired_call = None
        self._paired_result = None
        self._current_turn_index = None
        self._subagent_run = None
        self._job_mate = None
        self._schedule = None
        self._workflow = None
        self._sync_detail_sections([])
        self._sync_workflow_children()

    def get_plain_text(self) -> str:
        """Plain text of the current detail body (for clipboard yank).

        Rebuilds the event without display mid-caps so ``y`` is not limited to
        the truncated on-screen tool/message bodies. Falls back to the widget
        full plain cache when no event is loaded.
        """
        ev = self._current_event
        if ev is None and self._workflow is not None:
            return plain_from_renderable(render_workflow_detail(self._workflow), full=True)
        if ev is not None:
            renderable = render_event_detail(
                ev,
                duration=self._current_duration,
                paired_call=self._paired_call,
                paired_result=self._paired_result,
                turn_index=self._current_turn_index,
                subagent_run=self._subagent_run,
                job_mate=self._job_mate,
                schedule=self._schedule,
                workflow=self._workflow,
                truncate=False,
            )
            return plain_from_renderable(renderable, full=True)
        return self.visible_plain()
