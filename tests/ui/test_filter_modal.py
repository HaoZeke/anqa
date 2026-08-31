"""Saved filter pick, save, and hole forms."""

from __future__ import annotations

import pytest
from anqa.filters import FilterHole, SavedFilter, upsert_filter
from anqa.ui.filter_modal import (
    FilterHolesModal,
    FilterPickModal,
    FilterSaveModal,
    apply_named_filter,
    delete_named_filter,
    save_named_filter,
)
from anqa.ui.i18n import setup_i18n
from textual.app import App, ComposeResult
from textual.pilot import Pilot
from textual.widgets import Button, Input, Select, Static

from .pilot_helpers import wait_until


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield Static("main")


async def _wait_id(pilot: Pilot[None], widget_id: str) -> None:
    await wait_until(
        pilot,
        lambda: bool(list(pilot.app.screen.query(f"#{widget_id}"))),
        description=widget_id,
    )


@pytest.fixture(autouse=True)
def _i18n() -> None:
    setup_i18n("en")


@pytest.mark.asyncio
async def test_pick_modal_returns_name() -> None:
    app = _Host()
    result_holder: list[object] = []
    rows = [SavedFilter("Awaiting notes", "catalog", "has:note AND is:awaiting")]
    async with app.run_test(size=(80, 30)) as pilot:
        app.push_screen(FilterPickModal(rows), callback=result_holder.append)
        await _wait_id(pilot, "filter-pick-ok")
        app.screen.query_one("#filter-pick-ok", Button).press()
        await wait_until(pilot, lambda: result_holder == ["Awaiting notes"], description="picked")


@pytest.mark.asyncio
async def test_save_modal_returns_name() -> None:
    app = _Host()
    result_holder: list[object] = []
    async with app.run_test(size=(80, 30)) as pilot:
        app.push_screen(FilterSaveModal(), callback=result_holder.append)
        await _wait_id(pilot, "filter-save-input")
        app.screen.query_one("#filter-save-input", Input).value = "Awaiting notes"
        app.screen.action_save()
        await wait_until(pilot, lambda: result_holder == ["Awaiting notes"], description="named")


@pytest.mark.asyncio
async def test_holes_modal_returns_answers() -> None:
    app = _Host()
    result_holder: list[object] = []
    found = [
        FilterHole("harness", "choice", ("grok", "claude")),
        FilterHole("in", "text", ()),
    ]
    async with app.run_test(size=(80, 30)) as pilot:
        app.push_screen(FilterHolesModal(found), callback=result_holder.append)
        await _wait_id(pilot, "filter-holes-ok")
        app.screen.query_one("#filter-hole-in", Input).value = "~/src"
        app.screen.query_one("#filter-holes-ok", Button).press()
        await wait_until(pilot, lambda: len(result_holder) == 1, description="filled")
    assert result_holder == [{"harness": "grok", "in": "~/src"}]


@pytest.mark.asyncio
async def test_apply_named_filter_writes_query() -> None:
    upsert_filter(SavedFilter("Awaiting notes", "catalog", "has:note AND is:awaiting"))
    app = _Host()
    applied: list[str] = []
    async with app.run_test(size=(80, 30)) as pilot:
        apply_named_filter(app, "catalog", applied.append)
        await _wait_id(pilot, "filter-pick-ok")
        app.screen.query_one("#filter-pick-ok", Button).press()
        await wait_until(
            pilot, lambda: applied == ["has:note AND is:awaiting"], description="applied"
        )


@pytest.mark.asyncio
async def test_save_named_filter_writes_store() -> None:
    from anqa.filters import load_filters

    app = _Host()
    async with app.run_test(size=(80, 30)) as pilot:
        save_named_filter(app, "catalog", "has:note")
        await _wait_id(pilot, "filter-save-input")
        app.screen.query_one("#filter-save-input", Input).value = "Notes"
        app.screen.action_save()
        await wait_until(pilot, lambda: load_filters() != [], description="stored")
    assert load_filters() == [SavedFilter("Notes", "catalog", "has:note")]


@pytest.mark.asyncio
async def test_delete_named_filter_removes_row() -> None:
    from anqa.filters import load_filters

    upsert_filter(SavedFilter("Notes", "catalog", "has:note"))
    app = _Host()
    async with app.run_test(size=(80, 30)) as pilot:
        delete_named_filter(app, "catalog")
        await _wait_id(pilot, "filter-pick-ok")
        app.screen.query_one("#filter-pick-ok", Button).press()
        await wait_until(pilot, lambda: load_filters() == [], description="removed")


@pytest.mark.asyncio
async def test_apply_named_filter_fills_holes() -> None:
    upsert_filter(SavedFilter("Harness", "catalog", "harness:{grok,claude}"))
    app = _Host()
    applied: list[str] = []
    async with app.run_test(size=(80, 30)) as pilot:
        apply_named_filter(app, "catalog", applied.append)
        await _wait_id(pilot, "filter-pick-ok")
        app.screen.query_one("#filter-pick-ok", Button).press()
        await _wait_id(pilot, "filter-holes-ok")
        assert app.screen.query_one("#filter-hole-harness", Select).value == "grok"
        app.screen.query_one("#filter-holes-ok", Button).press()
        await wait_until(pilot, lambda: applied == ["harness:grok"], description="expanded")
