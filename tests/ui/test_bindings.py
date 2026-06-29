"""Bindings module: ChromeActions, focus_primary_list, open_jobs_on_app."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from groket.ui.bindings import (
    APP_SESSIONS,
    BROWSER,
    CAPABILITY_PICKER,
    FORM_SAVE,
    GLOBAL_ALWAYS,
    JOBS_MODAL,
    LIST_SELECT,
    MODAL_DISMISS,
    PERSONA_EDITOR,
    PERSONAS,
    RULES,
    RUN_CONFIGS,
    RUNNER,
    SESSION_SEARCH_MODAL,
    ChromeActions,
    focus_primary_list,
    open_jobs_on_app,
)
from textual.app import App, ComposeResult
from textual.widgets import Static


class TestBindingTuples:
    def test_all_tuples_nonempty(self) -> None:
        for name, tup in [
            ("GLOBAL_ALWAYS", GLOBAL_ALWAYS),
            ("APP_SESSIONS", APP_SESSIONS),
            ("BROWSER", BROWSER),
            ("RUNNER", RUNNER),
            ("RUN_CONFIGS", RUN_CONFIGS),
            ("CAPABILITY_PICKER", CAPABILITY_PICKER),
            ("PERSONAS", PERSONAS),
            ("PERSONA_EDITOR", PERSONA_EDITOR),
            ("FORM_SAVE", FORM_SAVE),
            ("RULES", RULES),
            ("MODAL_DISMISS", MODAL_DISMISS),
            ("JOBS_MODAL", JOBS_MODAL),
            ("LIST_SELECT", LIST_SELECT),
            ("SESSION_SEARCH_MODAL", SESSION_SEARCH_MODAL),
        ]:
            assert len(tup) > 0, f"{name} should not be empty"


class TestFocusPrimaryList:
    def test_with_none(self) -> None:
        focus_primary_list(None)  # type: ignore[arg-type]  # deliberate wrong type

    def test_with_unfocusable(self) -> None:
        w = SimpleNamespace(can_focus=False, parent=None)
        focus_primary_list(w)  # type: ignore[arg-type]  # stub for test

    def test_with_focusable_parent(self) -> None:
        parent = SimpleNamespace(can_focus=True, focus=MagicMock())
        child = SimpleNamespace(can_focus=False, parent=parent)
        focus_primary_list(child)  # type: ignore[arg-type]  # stub for test

    def test_with_data_table_like(self) -> None:
        widget = SimpleNamespace(
            can_focus=True,
            focus=MagicMock(),
            cursor_type="cell",
            row_count=3,
            move_cursor=MagicMock(),
            cursor_row=0,
        )
        focus_primary_list(widget)  # type: ignore[arg-type]  # stub for test
        widget.focus.assert_called_once()
        assert widget.cursor_type == "row"

    def test_with_empty_table(self) -> None:
        widget = SimpleNamespace(
            can_focus=True,
            focus=MagicMock(),
            cursor_type="cell",
            row_count=0,
            move_cursor=MagicMock(),
        )
        focus_primary_list(widget)  # type: ignore[arg-type]  # stub for test

    def test_focus_not_callable(self) -> None:
        widget = SimpleNamespace(can_focus=True, focus="not_callable")
        focus_primary_list(widget)  # type: ignore[arg-type]  # stub for test

    def test_negative_cursor_row(self) -> None:
        widget = SimpleNamespace(
            can_focus=True,
            focus=MagicMock(),
            cursor_type="cell",
            row_count=5,
            move_cursor=MagicMock(),
            cursor_row=-1,
        )
        focus_primary_list(widget)  # type: ignore[arg-type]  # stub for test
        widget.move_cursor.assert_called()

    def test_cursor_row_beyond_count(self) -> None:
        widget = SimpleNamespace(
            can_focus=True,
            focus=MagicMock(),
            cursor_type="cell",
            row_count=2,
            move_cursor=MagicMock(),
            cursor_row=5,
        )
        focus_primary_list(widget)  # type: ignore[arg-type]  # stub for test
        widget.move_cursor.assert_called()


class TestOpenJobsOnApp:
    def test_with_action(self) -> None:
        mock_fn = MagicMock()
        screen = SimpleNamespace(app=SimpleNamespace(action_open_jobs=mock_fn))
        open_jobs_on_app(screen)  # type: ignore[arg-type]  # stub for test
        mock_fn.assert_called_once()

    def test_without_action(self) -> None:
        screen = SimpleNamespace(app=SimpleNamespace())
        open_jobs_on_app(screen)  # type: ignore[arg-type]  # stub for test


class TestChromeActions:
    @pytest.mark.asyncio
    async def test_action_show_help(self) -> None:
        class HelpApp(App):
            def compose(self) -> ComposeResult:
                yield Static("hi")

        app = HelpApp()
        async with app.run_test():
            screen = app.screen
            ca = ChromeActions.__dict__["action_show_help"]
            ca(screen)

    @pytest.mark.asyncio
    async def test_action_self_test_no_op(self) -> None:
        class STApp(App):
            def compose(self) -> ComposeResult:
                yield Static("hi")

        app = STApp()
        async with app.run_test():
            screen = app.screen
            ca = ChromeActions.__dict__["action_self_test"]
            ca(screen)

    @pytest.mark.asyncio
    async def test_action_self_test_callable(self) -> None:
        """action_self_test delegates to app when callable is found."""
        called = []

        class STApp2(App):
            def compose(self) -> ComposeResult:
                yield Static("hi")

            def action_self_test(self) -> None:
                called.append(True)

        app = STApp2()
        async with app.run_test():
            screen = app.screen
            ca = ChromeActions.__dict__["action_self_test"]
            ca(screen)
            assert called

    @pytest.mark.asyncio
    async def test_action_open_jobs_callable(self) -> None:
        """action_open_jobs delegates to app."""
        called = []

        class JobsApp(App):
            def compose(self) -> ComposeResult:
                yield Static("hi")

            def action_open_jobs(self) -> None:
                called.append(True)

        app = JobsApp()
        async with app.run_test():
            screen = app.screen
            ca = ChromeActions.__dict__["action_open_jobs"]
            ca(screen)
            assert called


class TestFocusPrimaryListCursorReassert:
    def test_valid_cursor_reasserted(self) -> None:
        """Valid cursor_row is re-asserted with move_cursor."""
        widget = SimpleNamespace(
            can_focus=True,
            focus=MagicMock(),
            cursor_type="cell",
            row_count=5,
            move_cursor=MagicMock(),
            cursor_row=2,
        )
        focus_primary_list(widget)  # type: ignore[arg-type]  # stub for test
        widget.move_cursor.assert_called_with(row=2, column=0)
