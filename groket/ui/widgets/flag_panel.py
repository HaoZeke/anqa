"""Flag panel widget for adding/editing event flags."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.markup import escape as rich_escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, Static, TextArea

from ...models import Flag, FlagVerdict, TraceEvent
from .. import text as U
from ..bindings import FORM_SAVE
from ..i18n import t


class FlagModal(ModalScreen):
    """Modal dialog for flagging a trace event."""

    BINDINGS = list(FORM_SAVE)

    class FlagSubmitted(Message):
        def __init__(self, flag: Flag) -> None:
            super().__init__()
            self.flag = flag

    class FlagDeleted(Message):
        def __init__(self, event_index: int) -> None:
            super().__init__()
            self.event_index = event_index

    def __init__(self, event: TraceEvent, existing_flag: Flag | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.event = event
        self.existing_flag = existing_flag

    def compose(self) -> ComposeResult:
        ev = self.event
        title = U.edit_flag_title() if self.existing_flag else U.flag_event_title()
        with Vertical(id="modal-container"):
            yield Static(f"[bold]{title}[/bold]")
            yield Static(
                f"{t('ui-event')}{ev.index} | {ev.type_label} | {rich_escape(ev.tool_name or ev.event_type)}[/dim]"
            )
            yield Static(f"[dim]{rich_escape(ev.summary_line[:80])}[/dim]")
            yield Static("")
            yield Label(U.verdict_label())
            yield Select(
                [(v.value.replace("_", " ").title(), v.value) for v in FlagVerdict],
                value=self.existing_flag.verdict.value
                if self.existing_flag
                else FlagVerdict.BAD.value,
                id="verdict-select",
                classes="field-select",
            )
            yield Label(U.description_label())
            yield TextArea(
                self.existing_flag.description if self.existing_flag else "", id="flag-description"
            )
            with Horizontal(id="flag-buttons"):
                yield Button(U.save(), variant="primary", id="save-flag")
                if self.existing_flag:
                    yield Button(U.delete(), variant="error", id="delete-flag")
                yield Button(U.cancel(), id="cancel-flag")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        self._commit_save()

    def _commit_save(self) -> None:
        verdict_select = self.query_one("#verdict-select", Select)
        desc_area = self.query_one("#flag-description", TextArea)
        flag = Flag(
            event_index=self.event.index,
            verdict=FlagVerdict(str(verdict_select.value)),
            description=desc_area.text.strip(),
            event_type=self.event.event_type,
            tool_name=self.event.tool_name,
            tool_call_id=self.event.tool_call_id,
            timestamp=self.event.timestamp,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.dismiss(("save", flag))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-flag":
            self._commit_save()
        elif event.button.id == "delete-flag":
            self.dismiss(("delete", self.event.index))
        elif event.button.id == "cancel-flag":
            self.dismiss(None)
