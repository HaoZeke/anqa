"""Import session fuzzy modal tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.session.import_session import HostSessionRow
from groket.ui.i18n import setup_i18n
from groket.ui.import_session_modal import ImportSessionModal
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Input, Static

from .pilot_helpers import wait_until


class _HostApp(App):
    def compose(self) -> ComposeResult:
        from textual.widgets import Static as S

        yield S("main")


@pytest.mark.asyncio
async def test_import_modal_filters_and_submits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_i18n("en")
    rows = [
        HostSessionRow(
            path=tmp_path / "a" / "sid-a",
            session_id="sid-a",
            title="Alpha project work",
            cwd_label="/home/ali/alpha",
            mtime=100.0,
        ),
        HostSessionRow(
            path=tmp_path / "b" / "sid-b",
            session_id="sid-b",
            title="Beta other",
            cwd_label="/home/ali/beta",
            mtime=200.0,
        ),
    ]
    monkeypatch.setattr(
        "groket.ui.import_session_modal.list_host_grok_sessions",
        lambda *a, **k: rows,
    )

    app = _HostApp()
    result_holder: list[object] = []

    async with app.run_test(size=(120, 40)) as pilot:
        modal = ImportSessionModal()
        app.push_screen(modal, callback=lambda r: result_holder.append(r))
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, ImportSessionModal) and app.screen._loaded,
            description="import modal loaded",
        )
        table = app.screen.query_one("#import-session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count == 2, description="two rows")

        search = app.screen.query_one("#import-session-search", Input)
        search.focus()
        # Simulate typing so Input.Changed drives the filter (not only .value).
        await pilot.press(*list("alpha"))
        await wait_until(pilot, lambda: table.row_count == 1, description="filtered to alpha")
        assert app.screen._query_text == "alpha"

        # Path-fragment filter on the other row after clearing.
        search.value = ""
        app.screen._query_text = ""
        app.screen._apply_filter()
        await wait_until(pilot, lambda: table.row_count == 2, description="reset")
        await pilot.press(*list("beta"))
        await wait_until(pilot, lambda: table.row_count == 1, description="filtered to beta path")

        app.screen.action_submit()
        await wait_until(pilot, lambda: len(result_holder) == 1, description="submit fired")
        assert result_holder[0] == (str(rows[1].path), False)


@pytest.mark.asyncio
async def test_import_modal_shows_loading_then_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    setup_i18n("en")
    monkeypatch.setattr(
        "groket.ui.import_session_modal.list_host_grok_sessions",
        lambda *a, **k: [],
    )
    app = _HostApp()
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(ImportSessionModal())
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, ImportSessionModal) and app.screen._loaded,
            description="loaded empty",
        )
        status_w = app.screen.query_one("#import-session-status", Static)
        status = (
            status_w.render().plain
            if hasattr(status_w.render(), "plain")
            else str(status_w.render())
        )
        assert "No host sessions" in status or "no host" in status.lower()
