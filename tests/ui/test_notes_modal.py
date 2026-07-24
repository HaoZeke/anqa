"""Operator notes modal tests (create, edit, delete, pick)."""

from __future__ import annotations

import pytest
from groket.notes import FieldSpec, NoteEntry, default_schema
from groket.ui.i18n import setup_i18n
from groket.ui.widgets.notes_modal import (
    NotesModal,
    NotesPickModal,
    _note_preview_label,
    note_field_label,
)
from textual.app import App, ComposeResult
from textual.widgets import Button, Static, TextArea

from .pilot_helpers import wait_until


class _NoteApp(App):
    def compose(self) -> ComposeResult:
        yield Static("main")


@pytest.mark.asyncio
async def test_notes_modal_create_and_save() -> None:
    app = _NoteApp()
    result_holder: list[object] = []

    async with app.run_test(size=(100, 40)) as pilot:
        modal = NotesModal(
            schema=default_schema(),
            turn_options=[("Turn 0", "0"), ("Turn 1", "1")],
            default_turn=1,
            event_indices=[9],
        )
        app.push_screen(modal, callback=lambda r: result_holder.append(r))
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, NotesModal),
            description="NotesModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#note-field-summary"))),
            description="summary field mounted",
        )
        summary = app.screen.query_one("#note-field-summary", TextArea)
        summary.text = "missed gate"
        detail = app.screen.query_one("#note-field-detail", TextArea)
        detail.text = "should have verified"
        app.screen.action_save()
        await wait_until(
            pilot,
            lambda: len(result_holder) == 1,
            description="save callback fired",
        )
        result = result_holder[0]
        assert isinstance(result, tuple)
        assert result[0] == "save"
        entry = result[1]
        assert isinstance(entry, NoteEntry)
        assert entry.turn_index == 1
        assert entry.fields["summary"] == "missed gate"
        assert entry.event_indices == [9]
        assert (
            not list(app.screen.query("#delete-note"))
            if isinstance(app.screen, NotesModal)
            else True
        )


@pytest.mark.asyncio
async def test_notes_modal_edit_shows_delete_and_prefills() -> None:
    app = _NoteApp()
    existing = NoteEntry(
        id="n-edit1",
        turn_index=2,
        fields={"summary": "was summary", "detail": "was detail", "custom": "keep"},
        event_indices=[3],
        created_at="2020-01-01T00:00:00+00:00",
        updated_at="2020-01-01T00:00:00+00:00",
    )
    async with app.run_test(size=(100, 40)) as pilot:
        modal = NotesModal(
            schema=default_schema(),
            turn_options=[("Turn 0", "0"), ("Turn 2", "2")],
            existing=existing,
        )
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, NotesModal),
            description="NotesModal edit mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#delete-note"))),
            description="delete button mounted",
        )
        assert app.screen.existing is existing
        assert len(list(app.screen.query("#delete-note"))) == 1
        summary = app.screen.query_one("#note-field-summary", TextArea)
        assert summary.text == "was summary"
        detail = app.screen.query_one("#note-field-detail", TextArea)
        assert detail.text == "was detail"


@pytest.mark.asyncio
async def test_notes_modal_delete_dismisses_id() -> None:
    app = _NoteApp()
    result_holder: list[object] = []
    existing = NoteEntry(
        id="n-del1",
        turn_index=0,
        fields={"summary": "x"},
        event_indices=[],
        created_at="2020-01-01T00:00:00+00:00",
        updated_at="2020-01-01T00:00:00+00:00",
    )
    async with app.run_test(size=(100, 40)) as pilot:
        modal = NotesModal(
            schema=default_schema(),
            turn_options=[("Turn 0", "0")],
            existing=existing,
        )
        app.push_screen(modal, callback=lambda r: result_holder.append(r))
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, NotesModal),
            description="NotesModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#delete-note"))),
            description="delete button mounted",
        )
        del_btn = app.screen.query_one("#delete-note", Button)
        app.screen.on_button_pressed(Button.Pressed(del_btn))
        await wait_until(
            pilot,
            lambda: len(result_holder) == 1,
            description="delete callback fired",
        )
        assert result_holder[0] == ("delete", "n-del1")


@pytest.mark.asyncio
async def test_notes_modal_edit_save_preserves_id_and_merges() -> None:
    app = _NoteApp()
    result_holder: list[object] = []
    existing = NoteEntry(
        id="n-merge1",
        turn_index=1,
        fields={"summary": "old", "detail": "old-d", "custom_key": "keep-me"},
        event_indices=[8, 9],
        created_at="2019-06-01T12:00:00+00:00",
        updated_at="2019-06-01T12:00:00+00:00",
    )
    async with app.run_test(size=(100, 40)) as pilot:
        modal = NotesModal(
            schema=default_schema(),
            turn_options=[("Turn 0", "0"), ("Turn 1", "1"), ("Turn 3", "3")],
            existing=existing,
        )
        app.push_screen(modal, callback=lambda r: result_holder.append(r))
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, NotesModal),
            description="NotesModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#note-field-summary"))),
            description="fields mounted",
        )
        summary = app.screen.query_one("#note-field-summary", TextArea)
        summary.text = "new summary"
        detail = app.screen.query_one("#note-field-detail", TextArea)
        detail.text = "new detail"
        # Change turn via Select value.
        from textual.widgets import Select

        turn_sel = app.screen.query_one("#note-turn-select", Select)
        turn_sel.value = "3"
        save_btn = app.screen.query_one("#save-note", Button)
        app.screen.on_button_pressed(Button.Pressed(save_btn))
        await wait_until(
            pilot,
            lambda: len(result_holder) == 1,
            description="save callback fired",
        )
        action, entry = result_holder[0]  # type: ignore[misc]
        assert action == "save"
        assert isinstance(entry, NoteEntry)
        assert entry.id == "n-merge1"
        assert entry.created_at == "2019-06-01T12:00:00+00:00"
        assert entry.event_indices == [8, 9]
        assert entry.turn_index == 3
        assert entry.fields["summary"] == "new summary"
        assert entry.fields["detail"] == "new detail"
        assert entry.fields["custom_key"] == "keep-me"
        assert entry.updated_at != existing.updated_at
        assert entry.updated_at  # non-empty ISO


@pytest.mark.asyncio
async def test_notes_pick_modal_select() -> None:
    app = _NoteApp()
    a = NoteEntry.new(turn_index=0, fields={"summary": "first note text"}, note_id="n-a")
    b = NoteEntry.new(turn_index=1, fields={"summary": "second"}, note_id="n-b")
    result_holder: list[object] = []

    async with app.run_test(size=(100, 40)) as pilot:
        modal = NotesPickModal(notes=[a, b])
        app.push_screen(modal, callback=lambda r: result_holder.append(r))
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, NotesPickModal),
            description="NotesPickModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#pick-note-ok"))),
            description="ok button mounted",
        )
        ok_btn = app.screen.query_one("#pick-note-ok", Button)
        app.screen.on_button_pressed(Button.Pressed(ok_btn))
        await wait_until(
            pilot,
            lambda: len(result_holder) == 1,
            description="pick callback fired",
        )
        assert result_holder[0] is a or result_holder[0] is b
        assert isinstance(result_holder[0], NoteEntry)


@pytest.mark.asyncio
async def test_notes_pick_modal_cancel() -> None:
    app = _NoteApp()
    a = NoteEntry.new(turn_index=0, fields={"summary": "first note text"}, note_id="n-a")
    b = NoteEntry.new(turn_index=1, fields={"summary": "second"}, note_id="n-b")
    result_holder: list[object] = []

    async with app.run_test(size=(100, 40)) as pilot:
        modal = NotesPickModal(notes=[a, b])
        app.push_screen(modal, callback=lambda r: result_holder.append(r))
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, NotesPickModal),
            description="NotesPickModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#pick-note-cancel"))),
            description="cancel mounted",
        )
        cancel_btn = app.screen.query_one("#pick-note-cancel", Button)
        app.screen.on_button_pressed(Button.Pressed(cancel_btn))
        await wait_until(
            pilot,
            lambda: len(result_holder) == 1,
            description="cancel callback fired",
        )
        assert result_holder[0] is None


def test_note_preview_label_truncates() -> None:
    long = "x" * 80
    note = NoteEntry.new(turn_index=4, fields={"summary": long}, note_id="n-p")
    label = _note_preview_label(note)
    assert "…" in label
    assert len(label) < 80 + 20  # turn prefix + truncated


def test_note_field_label_resolves_fluent_defaults() -> None:
    setup_i18n("en")
    assert note_field_label(FieldSpec(id="summary")) == "Summary"
    assert note_field_label(FieldSpec(id="detail")) == "Detail"
    assert note_field_label(FieldSpec(id="severity", label="Severity")) == "Severity"
    for spec in default_schema().fields:
        assert note_field_label(spec) in ("Summary", "Detail")
