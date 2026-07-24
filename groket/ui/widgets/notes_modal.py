"""Modal for creating, editing, and deleting turn-linked operator notes."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.markup import escape as rich_escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, Static, TextArea

from ...notes import FieldSpec, NoteEntry, NotesSchema
from .. import text as U
from ..bindings import FORM_SAVE
from ..i18n import join_ui, t
from ..quit_actions import QuitActions

_PREVIEW_MAX = 60


def _note_preview_label(note: NoteEntry) -> str:
    """Turn label plus first non-empty field value (truncated)."""
    turn = t("turn-filter-n", n=note.turn_index)
    preview = ""
    for val in note.fields.values():
        text = (val or "").strip()
        if text:
            preview = text
            break
    if not preview:
        return turn
    if len(preview) > _PREVIEW_MAX:
        preview = preview[: _PREVIEW_MAX - 3] + "..."
    return join_ui(turn, "—", preview)


class NotesModal(QuitActions, ModalScreen):
    """Modal dialog for one operator note (create or edit; schema-driven fields)."""

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
        self.default_turn = int(existing.turn_index if existing is not None else default_turn)
        self.event_indices = list(
            existing.event_indices if existing is not None else (event_indices or [])
        )
        self.existing = existing

    def compose(self) -> ComposeResult:
        title = U.edit_note_title() if self.existing is not None else U.new_note_title()
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
                if self.existing is not None:
                    yield Button(U.delete(), variant="error", id="delete-note")
                yield Button(U.cancel(), id="cancel-note")

    def _field_widgets(self, spec: FieldSpec) -> ComposeResult:
        yield Label(spec.label + (":" if not spec.label.endswith(":") else ""))
        # Widget ids use only schema field ids (sanitized at schema load).
        initial = ""
        if self.existing is not None:
            initial = self.existing.fields.get(spec.id, "")
        yield TextArea(initial, id=f"note-field-{spec.id}")

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_save(self) -> None:
        self._commit_save()

    def _read_field(self, spec: FieldSpec) -> str:
        return self.query_one(f"#note-field-{spec.id}", TextArea).text.strip()

    def _read_turn_index(self) -> int | None:
        turn_sel = self.query_one("#note-turn-select", Select)
        raw_turn = turn_sel.value
        if raw_turn is Select.BLANK or raw_turn is None:
            self.notify(U.note_turn_invalid(), severity="error")
            return None
        try:
            return int(str(raw_turn))
        except (TypeError, ValueError):
            self.notify(U.note_turn_invalid(), severity="error")
            return None

    def _schema_fields_from_form(self) -> dict[str, str]:
        return {spec.id: self._read_field(spec) for spec in self.schema.fields}

    def _commit_save(self) -> None:
        turn_index = self._read_turn_index()
        if turn_index is None:
            return
        form_fields = self._schema_fields_from_form()
        if self.existing is not None:
            fields = dict(self.existing.fields)
            fields.update(form_fields)
            entry = NoteEntry(
                id=self.existing.id,
                turn_index=turn_index,
                fields=fields,
                event_indices=list(self.existing.event_indices),
                created_at=self.existing.created_at,
                updated_at=datetime.now(UTC).isoformat(),
            )
        else:
            entry = NoteEntry.new(
                turn_index=turn_index,
                fields=form_fields,
                event_indices=list(self.event_indices),
            )
        self.dismiss(("save", entry))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-note":
            self._commit_save()
        elif event.button.id == "delete-note" and self.existing is not None:
            self.dismiss(("delete", self.existing.id))
        elif event.button.id == "cancel-note":
            self.dismiss(None)


class NotesPickModal(QuitActions, ModalScreen):
    """Minimal picker when several operator notes exist."""

    def __init__(self, notes: list[NoteEntry], **kwargs) -> None:
        super().__init__(**kwargs)
        self.notes = list(notes)
        self._by_id = {n.id: n for n in self.notes}

    def compose(self) -> ComposeResult:
        options = [(_note_preview_label(n), n.id) for n in self.notes]
        default = options[0][1] if options else Select.BLANK
        with Vertical(id="modal-container"):
            yield Static(f"[bold]{rich_escape(U.pick_note_title())}[/bold]")
            yield Select(
                options,
                value=default,
                id="pick-note-select",
                classes="field-select",
                allow_blank=False,
            )
            with Horizontal(id="pick-note-buttons", classes="modal-footer"):
                yield Button(U.done(), variant="primary", id="pick-note-ok")
                yield Button(U.cancel(), id="pick-note-cancel")

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def _commit_pick(self) -> None:
        sel = self.query_one("#pick-note-select", Select)
        raw = sel.value
        if raw is Select.BLANK or raw is None:
            self.dismiss(None)
            return
        note = self._by_id.get(str(raw))
        self.dismiss(note)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pick-note-ok":
            self._commit_pick()
        elif event.button.id == "pick-note-cancel":
            self.dismiss(None)
