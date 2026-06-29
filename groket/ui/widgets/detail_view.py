"""Detail view widget for displaying event details (Rich/Markdown/Syntax)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Static

from ...analysis.base import Finding
from ...models import Flag, TraceEvent
from ..render_detail import render_event_detail, set_static_renderable


class DetailView(VerticalScroll):
    """Shows detailed information about a selected trace event.

    Uses a single Static child whose content is replaced on each
    selection, avoiding the remove_children/mount race that causes
    'NoneType' render_strips errors in Textual.
    """

    class FlagRequested(Message):
        def __init__(self, event: TraceEvent) -> None:
            super().__init__()
            self.event = event

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._current_event: TraceEvent | None = None
        self._current_finding: Finding | None = None
        self._current_flag: Flag | None = None
        self._current_duration: float | None = None
        self._paired_call: TraceEvent | None = None
        self._paired_result: TraceEvent | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="detail-body")

    def show_event(
        self,
        event: TraceEvent,
        finding: Finding | None = None,
        flag: Flag | None = None,
        duration: float | None = None,
        *,
        paired_call: TraceEvent | None = None,
        paired_result: TraceEvent | None = None,
    ) -> None:
        self._current_event = event
        self._current_finding = finding
        self._current_flag = flag
        self._current_duration = duration
        self._paired_call = paired_call
        self._paired_result = paired_result
        self._refresh_content()

    def _refresh_content(self) -> None:
        ev = self._current_event
        body = self.query_one("#detail-body", Static)
        if ev is None:
            body.update("")
            return
        renderable = render_event_detail(
            ev,
            finding=self._current_finding,
            flag=self._current_flag,
            duration=self._current_duration,
            paired_call=self._paired_call,
            paired_result=self._paired_result,
        )
        set_static_renderable(body, renderable)
        self.scroll_home(animate=False)

    def clear_detail(self) -> None:
        self._current_event = None
        self._current_finding = None
        self._current_flag = None
        self._current_duration = None
        self._paired_call = None
        self._paired_result = None
        self.query_one("#detail-body", Static).update("")
