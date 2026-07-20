"""Modal for creating turn-linked operator notes (create-only)."""

from __future__ import annotations

from rich.markup import escape as rich_escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, Static, TextArea

from ...notes import FieldSpec, NoteEntry, NotesSchema
from .. import text as U
from ..bindings import FORM_SAVE
from ..i18n import t
from ..quit_actions import QuitActions


class NotesModal(QuitActions, ModalScreen):
    """Modal dialog for one new operator note (schema-driven fields)."""

    BINDINGS = list(FORM_SAVE)

    def __init__(
        self,
        *,
        schema: NotesSchema,
        turn_options: list[tuple[str, str]],
        default_turn: int = 0,
        event_indices: list[int] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.schema = schema
        self.turn_options = turn_options or [(t("turn-filter-n", n=0), "0")]
        self.default_turn = int(default_turn)
        self.event_indices = list(event_indices or [])

    def compose(self) -> ComposeResult:
        title = U.new_note_title()
        turn_val = str(self.default_turn)
        turn_choices = {v for _, v in self.turn_options}
        if turn_val not in turn_choices:
            self.turn_options = list(self.turn_options) + [
                (t("turn-filter-n", n=self.default_turn), turn_val)
            ]
            turn_choices.add(turn_val)
        turn_value = turn_val if turn_val in turn_choices else self.turn_options[0][1]
        with Vertical(id="modal-container"):
            with VerticalScroll(id="notes-modal-body"):
                yield Static(f"[bold]{rich_escape(title)}[/bold]")
                yield Static(
                    f"[dim]{rich_escape(U.notes_schema_hint(self.schema.schema_id))}[/dim]"
                )
                yield Static("")
                yield Label(U.turn_label())
                yield Select(
                    self.turn_options,
                    value=turn_value,
                    id="note-turn-select",
                    classes="field-select",
                    allow_blank=False,
                )
                if self.event_indices:
                    yield Static(
                        f"[dim]{rich_escape(U.notes_events_hint(', '.join(str(i) for i in self.event_indices)))}[/dim]"
                    )
                yield Static("")
                for spec in self.schema.fields:
                    yield from self._field_widgets(spec)
            with Horizontal(id="note-buttons", classes="modal-footer"):
                yield Button(U.save(), variant="primary", id="save-note")
                yield Button(U.cancel(), id="cancel-note")

    def _field_widgets(self, spec: FieldSpec) -> ComposeResult:
        yield Label(spec.label + (":" if not spec.label.endswith(":") else ""))
        # Widget ids use only schema field ids (sanitized at schema load).
        yield TextArea("", id=f"note-field-{spec.id}")

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_save(self) -> None:
        self._commit_save()

    def _read_field(self, spec: FieldSpec) -> str:
        return self.query_one(f"#note-field-{spec.id}", TextArea).text.strip()

    def _commit_save(self) -> None:
        turn_sel = self.query_one("#note-turn-select", Select)
        raw_turn = turn_sel.value
        if raw_turn is Select.BLANK or raw_turn is None:
            self.notify(U.note_turn_invalid(), severity="error")
            return
        try:
            turn_index = int(str(raw_turn))
        except (TypeError, ValueError):
            self.notify(U.note_turn_invalid(), severity="error")
            return
        fields = {spec.id: self._read_field(spec) for spec in self.schema.fields}
        entry = NoteEntry.new(
            turn_index=turn_index,
            fields=fields,
            event_indices=list(self.event_indices),
        )
        self.dismiss(entry)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-note":
            self._commit_save()
        elif event.button.id == "cancel-note":
            self.dismiss(None)
