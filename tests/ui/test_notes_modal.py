"""Operator notes modal tests (create-only)."""

from __future__ import annotations

import pytest
from groket.notes import NoteEntry, default_schema
from groket.ui.widgets.notes_modal import NotesModal
from textual.app import App, ComposeResult
from textual.widgets import Static, TextArea

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
        entry = result_holder[0]
        assert isinstance(entry, NoteEntry)
        assert entry.turn_index == 1
        assert entry.fields["summary"] == "missed gate"
        assert entry.event_indices == [9]
        assert (
            not list(app.screen.query("#delete-note"))
            if isinstance(app.screen, NotesModal)
            else True
        )
