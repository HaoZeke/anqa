"""Modal for creating/editing turn-linked operator notes."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.markup import escape as rich_escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from ...notes.models import FieldSpec, NoteEntry, NotesSchema
from .. import text as U
from ..bindings import FORM_SAVE
from ..i18n import t
from ..quit_actions import QuitActions


class NotesModal(QuitActions, ModalScreen):
    """Modal dialog for one operator note (schema-driven fields)."""

    BINDINGS = list(FORM_SAVE)

    def __init__(
        self,
        *,
        schema: NotesSchema,
        turn_options: list[tuple[str, str]],
        default_turn: int = 0,
        event_indices: list[int] | None = None,
        existing: NoteEntry | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.schema = schema
        self.turn_options = turn_options or [(t("turn-filter-n", n=0), "0")]
        self.default_turn = int(default_turn)
        self.event_indices = list(event_indices or [])
        self.existing = existing

    def compose(self) -> ComposeResult:
        title = U.edit_note_title() if self.existing else U.new_note_title()
        turn_val = str(self.existing.turn_index if self.existing else self.default_turn)
        valid_turns = {v for _, v in self.turn_options}
        if turn_val not in valid_turns:
            self.turn_options = list(self.turn_options) + [
                (t("turn-filter-n", n=int(turn_val)), turn_val)
            ]
        with Vertical(id="modal-container"):
            with VerticalScroll(id="notes-modal-body"):
                yield Static(f"[bold]{rich_escape(title)}[/bold]")
                yield Static(
                    f"[dim]{rich_escape(U.notes_schema_hint(self.schema.schema_id))}[/dim]"
                )
                yield Static("")
                yield Label(U.turn_label())
                turn_choices = {v for _, v in self.turn_options}
                turn_value = turn_val if turn_val in turn_choices else self.turn_options[0][1]
                yield Select(
                    self.turn_options,
                    value=turn_value,
                    id="note-turn-select",
                    classes="field-select",
                    allow_blank=False,
                )
                if self.event_indices or (self.existing and self.existing.event_indices):
                    evs = self.existing.event_indices if self.existing else self.event_indices
                    yield Static(
                        f"[dim]{rich_escape(U.notes_events_hint(', '.join(str(i) for i in evs)))}[/dim]"
                    )
                yield Static("")
                for spec in self.schema.fields:
                    yield from self._field_widgets(spec)
            with Horizontal(id="note-buttons", classes="modal-footer"):
                yield Button(U.save(), variant="primary", id="save-note")
                if self.existing:
                    yield Button(U.delete(), variant="error", id="delete-note")
                yield Button(U.cancel(), id="cancel-note")

    def _field_widgets(self, spec: FieldSpec) -> ComposeResult:
        yield Label(spec.label + (":" if not spec.label.endswith(":") else ""))
        existing_val = ""
        if self.existing:
            existing_val = self.existing.fields.get(spec.id, "")
        widget_id = f"note-field-{spec.id}"
        if spec.choices:
            opts = [(c, c) for c in spec.choices]
            value = (
                existing_val
                if existing_val in spec.choices
                else (spec.choices[0] if spec.choices else "")
            )
            yield Select(
                opts,
                value=value,
                id=widget_id,
                classes="field-select",
                allow_blank=not spec.required,
            )
        elif spec.multiline:
            yield TextArea(existing_val, id=widget_id)
        else:
            yield Input(value=existing_val, id=widget_id, classes="field-input")

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_save(self) -> None:
        self._commit_save()

    def _read_field(self, spec: FieldSpec) -> str:
        widget_id = f"note-field-{spec.id}"
        if spec.choices:
            sel = self.query_one(f"#{widget_id}", Select)
            val = sel.value
            if val is Select.BLANK or val is None:
                return ""
            return str(val).strip()
        if spec.multiline:
            return self.query_one(f"#{widget_id}", TextArea).text.strip()
        return self.query_one(f"#{widget_id}", Input).value.strip()

    def _commit_save(self) -> None:
        turn_sel = self.query_one("#note-turn-select", Select)
        raw_turn = turn_sel.value
        try:
            turn_index = int(str(raw_turn))
        except (TypeError, ValueError):
            turn_index = self.default_turn
        fields: dict[str, str] = {}
        if self.existing:
            # Preserve unknown field keys from a newer/custom schema.
            fields.update(self.existing.fields)
        for spec in self.schema.fields:
            fields[spec.id] = self._read_field(spec)
        now = datetime.now(UTC).isoformat()
        if self.existing:
            entry = NoteEntry(
                id=self.existing.id,
                turn_index=turn_index,
                fields=fields,
                event_indices=list(self.existing.event_indices or self.event_indices),
                created_at=self.existing.created_at or now,
                updated_at=now,
            )
        else:
            entry = NoteEntry.new(
                turn_index=turn_index,
                fields=fields,
                event_indices=list(self.event_indices),
            )
        self.dismiss(("save", entry))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-note":
            self._commit_save()
        elif event.button.id == "delete-note" and self.existing:
            self.dismiss(("delete", self.existing.id))
        elif event.button.id == "cancel-note":
            self.dismiss(None)
