"""Bindings module: ChromeActions, focus_primary_list."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from anqa.ui.bindings import (
    APP_SESSIONS,
    BROWSER,
    CAPABILITY_PICKER,
    FORM_SAVE,
    GLOBAL_ALWAYS,
    LIST_SELECT,
    MODAL_CANCEL_QUIT,
    MODAL_DISMISS,
    SCREEN_CHROME,
    SESSION_HOME_ACTIONS,
    ChromeActions,
    focus_primary_list,
)
from textual.app import App, ComposeResult
from textual.widgets import Static


def _shown_actions(bindings: tuple) -> list[str]:
    return [b.action for b in bindings if b.show]


class TestBindingTuples:
    def test_all_tuples_nonempty(self) -> None:
        for name, tup in [
            ("GLOBAL_ALWAYS", GLOBAL_ALWAYS),
            ("APP_SESSIONS", APP_SESSIONS),
            ("BROWSER", BROWSER),
            ("CAPABILITY_PICKER", CAPABILITY_PICKER),
            ("FORM_SAVE", FORM_SAVE),
            ("MODAL_CANCEL_QUIT", MODAL_CANCEL_QUIT),
            ("MODAL_DISMISS", MODAL_DISMISS),
            ("LIST_SELECT", LIST_SELECT),
        ]:
            assert len(tup) > 0, f"{name} should not be empty"

    def test_footer_chrome_order_sessions_home(self) -> None:
        """Help first among chrome; Quit in global chrome; no Refresh in footer."""
        shown = _shown_actions(APP_SESSIONS)
        assert shown[0] == "show_help"
        assert "quit" in shown
        assert "refresh_context" not in shown
        assert "open_session" in shown
        assert "follow_up_sessions" not in shown
        assert "mark_sessions_done" not in shown
        assert not any(b.action == "follow_up_sessions" for b in APP_SESSIONS)
        assert not any(b.action == "mark_sessions_done" for b in APP_SESSIONS)

    def test_footer_chrome_order_pushed_screens(self) -> None:
        """Pushed screens: Help then Back then Quit. Refresh stays bound, off the rail."""
        shown = _shown_actions(SCREEN_CHROME)
        assert shown == ["show_help", "go_back", "quit"]
        assert "refresh_context" not in shown

    def test_browser_footer_is_session_actions(self) -> None:
        """Session rail: note, delete, copy, export — not next prompt or end session."""
        shown = set(_shown_actions(BROWSER))
        assert "delete_session" in shown
        assert "edit_operator_note" not in shown
        assert "toggle_event_reader" in shown
        assert "show_help" in shown
        assert "go_back" in shown
        assert "operator_note" in shown
        assert "copy_detail" in shown
        assert "export_bundle" in shown
        assert "focus_follow_up" not in shown
        assert "mark_session_done" not in shown
        assert not any(b.action == "focus_follow_up" for b in BROWSER)
        assert not any(b.action == "mark_session_done" for b in BROWSER)

    def test_browser_binds_four_pane_digits(self) -> None:
        actions = {b.action for b in BROWSER}
        assert "tab_pane_1" in actions
        assert "tab_pane_4" in actions
        assert "tab_pane_5" not in actions

    def test_global_always_includes_quit(self) -> None:
        assert "quit" in _shown_actions(GLOBAL_ALWAYS)

    def test_session_home_actions_covers_list_bindings(self) -> None:
        assert "quit" not in SESSION_HOME_ACTIONS  # global, not home-gated
        assert "open_session" in SESSION_HOME_ACTIONS
        assert "show_help" not in SESSION_HOME_ACTIONS
        assert "follow_up_sessions" not in SESSION_HOME_ACTIONS
        assert "mark_sessions_done" not in SESSION_HOME_ACTIONS


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
