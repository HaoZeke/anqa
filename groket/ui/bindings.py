"""Single source of truth for keyboard shortcuts and TUI navigation.

Screens import binding tuples from here — do not invent ad-hoc key lists in
banners, button labels, or one-off help strings. See AGENTS.md «TUI navigation».
"""

from __future__ import annotations

from contextlib import suppress

from textual.binding import Binding
from textual.screen import Screen
from textual.widget import Widget

from . import text as U
from .i18n import t
from .widgets.help_modal import notify_help


def _b(
    key: str, action: str, description: str, *, show: bool = True, priority: bool = False
) -> Binding:
    return Binding(key, action, description, show=show, priority=priority)


def _ctrl_s(action: str, description: str = t("ui-save"), *, show: bool = True) -> Binding:
    """Ctrl+S with priority — works while focus is in Input / TextArea / Select."""
    return _b("ctrl+s", action, description, show=show, priority=True)


# Priority hotkeys checked app-down before focused widgets (TextArea / Input).
# Ctrl+Enter often arrives as ctrl+j in terminals; bind both for launch while Runner is top.
APP_GLOBAL_PRIORITY: tuple[Binding, ...] = (
    _b(
        "ctrl+enter,ctrl+j",
        "launch_from_runner",
        U.bind_launch(),
        show=False,
        priority=True,
    ),
)

# Footer layout (left → right; command palette is always on the far right):
#   Help · Back (pushed screens) · context actions · Jobs · Quit (sessions home only)
# F5 / Ctrl+R still refresh; they stay out of the footer to keep it lean.
# App session-home bindings are gated in TraceEvalApp.check_action so they do
# not leak into Runner / Browser / etc. footers via binding inheritance.

GLOBAL_ALWAYS: tuple[Binding, ...] = (
    _b("?", "show_help", U.bind_help(), show=True),
    _b("f5", "refresh_context", U.bind_refresh(), show=False),
    _b("ctrl+r", "refresh_context", U.bind_refresh(), show=False),
    _b("j", "open_jobs", U.bind_jobs(), show=True),
    _b("ctrl+t", "self_test", t("ui-self-test"), show=False),
)
LIST_SELECT: tuple[Binding, ...] = (
    _b("s", "toggle_select", U.bind_select(), show=True),
    _b("space", "toggle_select", U.bind_select(), show=False),
)
LIST_SELECT_ALL: tuple[Binding, ...] = (
    _b("S", "select_all_toggle", U.bind_select_all(), show=False),
)
# Sessions home only — order: Help/Jobs chrome, primary list actions, Quit last.
APP_SESSIONS: tuple[Binding, ...] = GLOBAL_ALWAYS + (
    _b("enter", "open_session", U.bind_open(), show=True),
    _b("slash", "search_sessions", U.bind_search(), show=True),
    _b("r", "open_runner", U.bind_runner(), show=True),
    _b("C", "open_run_configs", U.bind_configs(), show=True),
    _b("P", "open_personas", U.bind_personas(), show=True),
    _b("s", "toggle_select", U.bind_select(), show=True),
    _b("space", "toggle_select", U.bind_select(), show=False),
    _b("S", "select_all", U.bind_select_all(), show=False),
    _b("R", "rerun_session", U.bind_rerun(), show=False),
    _ctrl_s("save_session_config", U.bind_save_cfg(), show=True),
    _b("x", "delete_sessions", U.bind_delete(), show=False),
    _b("delete", "delete_sessions", U.bind_delete(), show=False),
    _b("m", "cycle_model_filter", U.bind_model(), show=False),
    _b("a", "analyze", U.bind_analyze(), show=False),
    _b("d", "open_rules", U.bind_rules(), show=False),
    _b("t", "cycle_theme", U.bind_theme(), show=False),
    # Multi-turn: n = next prompt, e = end session (not Ctrl+Enter).
    _b("n", "follow_up_sessions", U.bind_next_prompt(), show=True),
    _b("e", "mark_sessions_done", U.bind_end_session(), show=True),
    _b("q", "quit", U.bind_quit(), show=True),
)
# Pushed screens: Help then Back in stable slots; Jobs; no Quit (home only).
SCREEN_CHROME: tuple[Binding, ...] = (
    _b("?", "show_help", U.bind_help(), show=True),
    _b("escape", "go_back", U.bind_back(), show=True),
    _b("f5", "refresh_context", U.bind_refresh(), show=False),
    _b("ctrl+r", "refresh_context", U.bind_refresh(), show=False),
    _b("j", "open_jobs", U.bind_jobs(), show=True),
    _b("ctrl+t", "self_test", t("ui-self-test"), show=False),
)
# App-level actions that only apply on the sessions home screen (not inherited UI).
SESSION_HOME_ACTIONS: frozenset[str] = frozenset(
    {
        "quit",
        "open_runner",
        "open_run_configs",
        "open_personas",
        "search_sessions",
        "open_session",
        "toggle_select",
        "select_all",
        "rerun_session",
        "save_session_config",
        "delete_sessions",
        "cycle_model_filter",
        "analyze",
        "open_rules",
        "cycle_theme",
        "follow_up_sessions",
        "mark_sessions_done",
    }
)
BROWSER: tuple[Binding, ...] = SCREEN_CHROME + (
    _b("left_square_bracket", "tab_prev", U.bind_prev_tab(), show=True),
    _b("right_square_bracket", "tab_next", U.bind_next_tab(), show=True),
    _b("1", "tab_timeline", U.bind_timeline(), show=False),
    _b("2", "tab_summary", U.bind_summary(), show=False),
    _b("3", "tab_diff", U.bind_diff(), show=False),
    _b("4", "tab_findings", U.bind_findings(), show=False),
    _b("5", "tab_report", U.bind_report(), show=False),
    _b("v", "focus_timeline_filter", U.bind_view(), show=False),
    _b("f", "flag_event", U.bind_flag(), show=True),
    _b("slash", "search", U.bind_search(), show=False),
    _b("c", "clear_filters", U.bind_clear_view(), show=False),
    _b("i", "tab_findings", U.bind_findings(), show=False),
    _b("x", "delete_session", U.bind_delete(), show=True),
    _b("delete", "delete_session", U.bind_delete(), show=False),
    _b("s", "open_share", U.bind_share(), show=False),
    # n = type next prompt (focus input); Enter in input sends; e = end session.
    _b("n", "focus_follow_up", U.bind_next_prompt(), show=True),
    _b("e", "mark_session_done", U.bind_end_session(), show=True),
)
RUNNER: tuple[Binding, ...] = SCREEN_CHROME + (
    # Priority + ctrl+j: many terminals map Ctrl+Enter to ctrl+j (or plain enter).
    # App also binds launch_from_runner with priority so TextArea cannot swallow it.
    _b("ctrl+enter,ctrl+j", "run_evaluation", U.bind_launch(), show=True, priority=True),
    _ctrl_s("save_config_only", U.bind_save(), show=True),
    _b("n", "new_persona_from_runner", U.bind_new_persona(), show=False),
    _b("p", "open_persona_builder", U.bind_personas(), show=False),
    _b("d", "check_docker", U.bind_docker(), show=False),
    _b("left_square_bracket", "tab_prev", U.bind_prev_pane(), show=True),
    _b("right_square_bracket", "tab_next", U.bind_next_pane(), show=True),
    _b("1", "tab_recipe", U.bind_recipe(), show=False),
    _b("2", "tab_runtime", U.bind_runtime(), show=False),
    _b("3", "tab_extras", U.bind_extras(), show=False),
)
RUN_CONFIGS: tuple[Binding, ...] = (
    SCREEN_CHROME
    + (
        _b("enter", "open_in_runner", U.bind_open(), show=True),
        _b("l", "launch_config", U.bind_launch(), show=True),
        _b("w", "launch_selected", U.bind_launch_selected(), show=True),
    )
    + LIST_SELECT
    + LIST_SELECT_ALL
    + (
        _b("x", "delete_config", U.bind_delete(), show=False),
        _b("n", "new_blank", U.bind_new(), show=False),
    )
)
CAPABILITY_PICKER: tuple[Binding, ...] = (
    _b("escape", "cancel", U.bind_cancel(), show=True),
    _b("s", "toggle_select", U.bind_select(), show=True),
    _b("space", "toggle_select", U.bind_select(), show=False),
    _ctrl_s("done", U.bind_done(), show=True),
)
PERSONAS: tuple[Binding, ...] = SCREEN_CHROME + (
    _b("n", "new_persona", U.bind_new(), show=True),
    _b("enter", "edit_persona", U.bind_edit(), show=True),
    _b("e", "edit_persona", U.bind_edit(), show=False),
    _b("x", "delete_persona", U.bind_delete(), show=True),
    _b("delete", "delete_persona", U.bind_delete(), show=False),
)
PERSONA_EDITOR: tuple[Binding, ...] = (
    _b("escape", "cancel", U.bind_cancel(), show=True),
    _ctrl_s("save", U.bind_save(), show=True),
    _b("left_square_bracket", "tab_prev", U.bind_prev_tab(), show=True),
    _b("right_square_bracket", "tab_next", U.bind_next_tab(), show=True),
    _b("1", "tab_identity", U.bind_identity(), show=False),
    _b("2", "tab_github", U.bind_github(), show=False),
    _b("3", "tab_env", U.bind_environment(), show=False),
    _b("4", "tab_mcp", U.bind_mcp(), show=False),
    _b("5", "tab_skills", U.bind_skills(), show=False),
    _b("6", "tab_plugins", U.bind_plugins(), show=False),
)
FORM_SAVE: tuple[Binding, ...] = (
    _b("escape", "cancel", U.bind_cancel(), show=True),
    _ctrl_s("save", U.bind_save(), show=True),
)
RULES: tuple[Binding, ...] = SCREEN_CHROME + (
    _b("t", "toggle_rule", U.bind_toggle(), show=True),
    _b("a", "enable_all", U.bind_enable_all(), show=False),
    _b("A", "disable_all", U.bind_disable_all(), show=False),
)
MODAL_DISMISS: tuple[Binding, ...] = (_b("escape", "dismiss", U.bind_cancel(), show=True),)
JOBS_MODAL: tuple[Binding, ...] = (
    _b("?", "show_help", U.bind_help(), show=True),
    _b("escape", "dismiss_modal", U.bind_close(), show=True),
    _b("j", "dismiss_modal", U.bind_close(), show=False),
    _b("f5", "refresh", U.bind_refresh(), show=False),
    _b("ctrl+r", "refresh", U.bind_refresh(), show=False),
    _b("enter", "open_session", U.bind_open(), show=True),
    _b("o", "open_session", U.bind_open(), show=False),
    _b("s", "open_share", U.bind_share(), show=True),
    _b("c", "clear_logs", U.bind_clear_logs(), show=False),
)
SESSION_SEARCH_MODAL: tuple[Binding, ...] = (
    _b("escape", "dismiss", U.bind_cancel(), show=True),
    _b("up", "cursor_up", t("ui-up"), show=False),
    _b("down", "cursor_down", t("ui-down"), show=False),
)


def blur_focused_edit(screen: Screen) -> bool:
    """If focus is in a common edit control, blur it and return True.

    Applies to Textual ``Input``, ``TextArea``, and ``Select`` (and subclasses)
    on *any* screen — not per-field wiring. Lets Esc leave the field so Tab /
    pane keys work; a second Esc still goes back / cancels.
    """
    focused = getattr(screen, "focused", None)
    if focused is None:
        return False
    # Local import avoids circular imports with widgets at module load.
    from textual.widgets import Input, Select, TextArea

    if not isinstance(focused, (Input, TextArea, Select)):
        return False
    with suppress(Exception):
        focused.blur()
    # Clear focus so the next key uses screen-level bindings.
    with suppress(Exception):
        screen.set_focus(None)
    return True


class ChromeActions(Screen):
    """Base for screens using SCREEN_CHROME (Esc / help / refresh / jobs).

    **Esc** blurs a focused Input / TextArea / Select first; then, if
    :meth:`form_is_dirty` is true, asks to discard edits; otherwise leaves.
    Override :meth:`_finish_leave` for post-confirm side effects (toasts),
    not :meth:`action_go_back`, unless you re-call :func:`blur_focused_edit`.
    """

    def action_show_help(self) -> None:
        notify_help(self)

    def action_open_jobs(self) -> None:
        open_jobs_on_app(self)

    def action_self_test(self) -> None:
        fn = getattr(self.app, "action_self_test", None)
        if callable(fn):
            fn()

    def form_is_dirty(self) -> bool:
        """True when leaving would lose uncommitted form edits (override on editors)."""
        return False

    def action_go_back(self) -> None:
        """Esc: blur edit controls first; otherwise leave the screen."""
        if blur_focused_edit(self):
            return
        self._leave_screen()

    def action_cancel(self) -> None:
        """Esc on modals that bind cancel — same blur-then-leave as go_back."""
        if blur_focused_edit(self):
            return
        self._leave_screen()

    def action_dismiss(self) -> None:
        """Esc on modals that bind dismiss."""
        if blur_focused_edit(self):
            return
        self._leave_screen()

    def _leave_screen(self) -> None:
        """Leave after optional discard confirmation when the form is dirty."""
        if self.form_is_dirty():
            from .confirm_modal import DiscardConfirmModal

            def _done(discard: bool | None) -> None:
                if discard:
                    self._finish_leave()

            self.app.push_screen(DiscardConfirmModal(), _done)
            return
        self._finish_leave()

    def _finish_leave(self) -> None:
        """Pop this screen (override for leave side effects after confirm)."""
        with suppress(Exception):
            if len(self.app.screen_stack) > 1:
                self.app.pop_screen()


def open_jobs_on_app(screen: Screen) -> None:
    fn = getattr(screen.app, "action_open_jobs", None)
    if callable(fn):
        fn()


def dismiss_after_blur(screen: Screen, result: object = None) -> None:
    """Esc on modals: blur Input/TextArea/Select first, else ``dismiss(result)``."""
    if blur_focused_edit(screen):
        return
    dirty_fn = getattr(screen, "form_is_dirty", None)
    if callable(dirty_fn) and dirty_fn():
        from .confirm_modal import DiscardConfirmModal

        def _done(discard: bool | None) -> None:
            if discard:
                with suppress(Exception):
                    screen.dismiss(result)  # type: ignore[attr-defined]

        screen.app.push_screen(DiscardConfirmModal(), _done)
        return
    with suppress(Exception):
        screen.dismiss(result)  # type: ignore[attr-defined]


def focus_primary_list(widget: Widget) -> None:
    """Give keyboard focus to a primary list/table after populate.

    DataTable often paints a row cursor (highlight) without focus; arrows and
    Enter then appear broken. Prefer this over leaving focus on path inputs
    or inert chrome when the main work surface is a list.

    Safe to call from ``call_after_refresh`` after TabbedContent switches panes
    (focusing a hidden pane's child is unreliable until layout runs).
    """
    if widget is None:
        return
    can_focus = getattr(widget, "can_focus", None)
    if can_focus is False:
        parent = getattr(widget, "parent", None)
        if parent is not None and getattr(parent, "can_focus", False):
            widget = parent
    focus = getattr(widget, "focus", None)
    if not callable(focus):
        return
    try:
        if hasattr(widget, "cursor_type"):
            with suppress(Exception):
                widget.cursor_type = "row"
        focus()
    except Exception:
        return
    row_count = getattr(widget, "row_count", None)
    move = getattr(widget, "move_cursor", None)
    if not callable(move) or not row_count:
        return
    with suppress(Exception):
        cursor_row = getattr(widget, "cursor_row", None)
        if cursor_row is None or cursor_row < 0 or cursor_row >= row_count:
            move(row=0, column=0)
        else:
            move(row=int(cursor_row), column=0)
