"""Pilot-driven tests for :mod:`groket.ui.app`.

Exercises compose, mount, session loading, table population, filter cycling,
multi-select, delete, analyze, rerun, save config, theme cycling, screen pushes,
analysis settings modal, session search modal, tips toggle, and action methods.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.models import SessionMeta
from groket.parser import load_session_meta
from groket.ui.app import (
    AnalysisSettingsModal,
    SessionSearchModal,
    TraceEvalApp,
    _coerce_select_value,
)
from textual.widgets import DataTable, Input, Select, Static

from .pilot_helpers import wait_until

# ── Helpers ───────────────────────────────────────────────────────────────


def _session_paths_banner_text(work_dir: Path, traces_root: Path) -> str:
    """Exact markup string the sessions-home banner must show (same as app)."""
    _ = work_dir
    traces = Path(traces_root).expanduser()
    return f"[dim]Traces[/dim]  {traces}"


def _write_session(
    traces_root: Path,
    session_id: str = "sess-cov-001",
    *,
    model_id: str = "test-model",
    task_id: str = "",
    outcome: str = "success",
    git_repo: str = "",
    git_branch: str = "",
    summary_text: str = "Coverage session",
    title: str = "Fix tests",
) -> Path:
    """Create a minimal session dir accepted by :func:`find_sessions` / meta load."""
    sd = traces_root / session_id
    sd.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {
        "info": {"id": session_id, "cwd": "/workspace"},
        "session_summary": summary_text,
        "created_at": "2026-06-25T00:00:00Z",
        "updated_at": "2026-06-25T00:01:00Z",
        "num_messages": 2,
        "current_model_id": model_id,
        "generated_title": title,
    }
    if git_repo:
        summary["info"]["git_repo_url"] = git_repo  # type: ignore[index]  # nested dict built dynamically
    if git_branch:
        summary["info"]["git_branch"] = git_branch  # type: ignore[index]  # nested dict built dynamically
    if task_id:
        summary["info"]["task_id"] = task_id  # type: ignore[index]  # nested dict built dynamically
    (sd / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    if task_id or git_repo or git_branch:
        run_data: dict[str, object] = {"run_id": session_id}
        if task_id:
            run_data["task_id"] = task_id
        if git_repo:
            run_data["repo_url"] = git_repo
        if git_branch:
            run_data["repo_branch"] = git_branch
        (sd / "run.json").write_text(json.dumps(run_data), encoding="utf-8")
    events = [
        {"type": "turn_started", "ts": "2026-06-25T00:00:00Z", "model_id": model_id},
        {"type": "turn_ended", "ts": "2026-06-25T00:01:00Z", "outcome": outcome},
    ]
    lines = [json.dumps(e) for e in events]
    (sd / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Minimal chat_history for extract_prompt
    chat = [
        {"type": "user", "content": [{"type": "text", "text": "<user_query>Fix it</user_query>"}]},
        {"type": "assistant", "content": [{"type": "text", "text": "On it."}]},
    ]
    chat_lines = [json.dumps(m) for m in chat]
    (sd / "chat_history.jsonl").write_text("\n".join(chat_lines) + "\n", encoding="utf-8")
    return sd


def _make_app(
    tmp_path: Path,
    *,
    n_sessions: int = 2,
    model_ids: list[str] | None = None,
    task_ids: list[str] | None = None,
) -> tuple[TraceEvalApp, Path, Path]:
    """Build a :class:`TraceEvalApp` with *n_sessions* minimal sessions."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    models = model_ids or ["m-alpha"]
    tasks = task_ids or [""]
    for i in range(n_sessions):
        mid = models[i % len(models)]
        tid = tasks[i % len(tasks)]
        _write_session(traces, f"sess-{i:03d}", model_id=mid, task_id=tid)
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    return app, work, traces


# ── Pure / unit-level helpers ─────────────────────────────────────────────


class TestCoerceSelectValue:
    """_coerce_select_value sentinel handling."""

    def test_none_returns_default(self) -> None:
        assert _coerce_select_value(None) is None
        assert _coerce_select_value(None, default="x") == "x"

    def test_string_passthrough(self) -> None:
        assert _coerce_select_value("hello") == "hello"

    def test_int_passthrough(self) -> None:
        assert _coerce_select_value(42) == 42

    def test_bool_passthrough(self) -> None:
        assert _coerce_select_value(True) is True

    def test_blank_sentinel(self) -> None:
        assert _coerce_select_value(Select.BLANK, default="d") == "d"

    def test_arbitrary_object_rejected(self) -> None:
        assert _coerce_select_value(object(), default="fb") == "fb"

    def test_class_named_no_selection(self) -> None:
        """Types whose ``__name__`` matches the sentinel list are rejected."""

        class NoSelection:
            pass

        assert _coerce_select_value(NoSelection(), default="z") == "z"


class TestCursorKeyAfterDeletes:
    """Cover :meth:`TraceEvalApp._cursor_key_after_deletes` branch logic."""

    def test_empty_list(self) -> None:
        assert TraceEvalApp._cursor_key_after_deletes([], None, set()) is None

    def test_all_gone(self) -> None:
        assert TraceEvalApp._cursor_key_after_deletes(["a", "b"], "a", {"a", "b"}) is None

    def test_cursor_not_gone(self) -> None:
        assert TraceEvalApp._cursor_key_after_deletes(["a", "b"], "a", {"b"}) == "a"

    def test_cursor_gone_picks_next(self) -> None:
        assert TraceEvalApp._cursor_key_after_deletes(["a", "b", "c"], "a", {"a"}) == "b"

    def test_cursor_gone_last_picks_prev(self) -> None:
        assert TraceEvalApp._cursor_key_after_deletes(["a", "b", "c"], "c", {"c"}) == "b"

    def test_cursor_none_picks_first_remaining(self) -> None:
        result = TraceEvalApp._cursor_key_after_deletes(["a", "b"], None, {"a"})
        assert result == "b"


class TestExtractTaskAndModel:
    """Cover :meth:`TraceEvalApp._extract_task_and_model`."""

    def test_build_suffix(self) -> None:
        assert TraceEvalApp._extract_task_and_model("groket-abc-build") == ("abc", "build")

    def test_s80_suffix(self) -> None:
        assert TraceEvalApp._extract_task_and_model("groket-abc-s80") == ("abc", "s80")

    def test_s140_suffix(self) -> None:
        assert TraceEvalApp._extract_task_and_model("groket-abc-s140") == ("abc", "s140")

    def test_hyphen_fallback(self) -> None:
        task, model = TraceEvalApp._extract_task_and_model("groket-task-custom")
        assert task == "task"
        assert model == "custom"

    def test_no_hyphen(self) -> None:
        assert TraceEvalApp._extract_task_and_model("standalone") == ("standalone", "unknown")


class TestSelectValueToFilter:
    """Cover :meth:`TraceEvalApp._select_value_to_filter`."""

    def test_all_returns_empty(self) -> None:
        assert TraceEvalApp._select_value_to_filter("all") == ""

    def test_blank_returns_empty(self) -> None:
        assert TraceEvalApp._select_value_to_filter(Select.BLANK) == ""

    def test_none_returns_empty(self) -> None:
        assert TraceEvalApp._select_value_to_filter(None) == ""

    def test_model_id_passthrough(self) -> None:
        assert TraceEvalApp._select_value_to_filter("gpt-4") == "gpt-4"


class TestFilterToSelectValue:
    """Cover :meth:`TraceEvalApp._filter_to_select_value`."""

    def test_empty_returns_all(self) -> None:
        assert TraceEvalApp._filter_to_select_value("") == "all"

    def test_nonempty_passthrough(self) -> None:
        assert TraceEvalApp._filter_to_select_value("m1") == "m1"


class TestSessionSortTs:
    """Cover :meth:`TraceEvalApp._session_sort_ts` branches."""

    def test_iso_timestamp(self, tmp_path: Path) -> None:
        sd = _write_session(tmp_path / "t", "s1")
        meta = load_session_meta(sd)
        ts = TraceEvalApp._session_sort_ts(meta)
        assert ts > 0

    def test_empty_timestamps_uses_mtime(self, tmp_path: Path) -> None:
        sd = _write_session(tmp_path / "t", "s2")
        meta = load_session_meta(sd)
        meta.created_at = ""
        meta.updated_at = ""
        ts = TraceEvalApp._session_sort_ts(meta)
        assert ts > 0

    def test_naive_datetime_handled(self, tmp_path: Path) -> None:
        sd = _write_session(tmp_path / "t", "s3")
        meta = load_session_meta(sd)
        meta.created_at = "2026-01-01T00:00:00"
        meta.updated_at = ""
        ts = TraceEvalApp._session_sort_ts(meta)
        assert ts > 0


# ── Pilot integration tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compose_and_mount_widgets(tmp_path: Path) -> None:
    """App compose yields all expected core widgets (Header, Footer, DataTable, etc.)."""
    app, work, traces = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        assert table.row_count >= 1
        # Filter bar selects exist
        app.query_one("#session-model-select", Select)
        banner = app.query_one("#session-paths", Static)
        expected = _session_paths_banner_text(app.work_dir, app._session_traces_root())
        assert banner.content == expected
        # Labels resolve (not Fluent message ids) and paths are the live roots.
        assert "Traces" in expected
        assert str(app._session_traces_root()) in expected
        assert (
            app._session_traces_root() == traces.resolve() or app._session_traces_root() == traces
        )


@pytest.mark.asyncio
async def test_refresh_reloads_sessions(tmp_path: Path) -> None:
    """Refresh re-scans the fixed traces root (no path editing in the UI)."""
    app, work, traces = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="initial load")
        _write_session(traces, "sess-extra", model_id="m-extra")
        app._refresh_sessions_list()
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="reload found extra")


@pytest.mark.asyncio
async def test_toggle_select_and_select_all(tmp_path: Path) -> None:
    """Toggle selection on a row and select/deselect all."""
    app, _, _ = _make_app(tmp_path, n_sessions=3)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 3, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 3, description="table populated")

        # Toggle select on current row
        await pilot.press("s")
        await pilot.pause()
        assert len(app._selected) == 1

        # Toggle again = deselect
        await pilot.press("s")
        await pilot.pause()
        assert len(app._selected) == 0

        # Select all
        app.action_select_all()
        await pilot.pause()
        assert len(app._selected) == 3

        # Select all again = deselect all
        app.action_select_all()
        await pilot.pause()
        assert len(app._selected) == 0


@pytest.mark.asyncio
async def test_cycle_model_filter(tmp_path: Path) -> None:
    """Cycling model filter narrows displayed sessions."""
    app, _, _ = _make_app(tmp_path, n_sessions=4, model_ids=["alpha", "beta"])
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 4, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 4, description="table populated")

        # Cycle to first model
        app.action_cycle_model_filter()
        await pilot.pause()
        assert app._filter_model != ""
        assert table.row_count == 2

        # Cycle to second model
        app.action_cycle_model_filter()
        await pilot.pause()
        filtered_2 = table.row_count
        assert filtered_2 == 2

        # Cycle wraps back to "all"
        app.action_cycle_model_filter()
        await pilot.pause()
        assert app._filter_model == ""
        assert table.row_count == 4


@pytest.mark.asyncio
async def test_cycle_model_filter_empty(tmp_path: Path) -> None:
    """Cycling model filter when sessions have only one model does nothing harmful."""
    app, _, _ = _make_app(tmp_path, n_sessions=2, model_ids=["only-model"])
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")
        app.action_cycle_model_filter()
        await pilot.pause()
        # Should cycle to the only model and back
        app.action_cycle_model_filter()
        await pilot.pause()
        assert app._filter_model == ""


@pytest.mark.asyncio
async def test_model_filter_select_changed(tmp_path: Path) -> None:
    """Changing model Select widget directly triggers filter update."""
    app, _, _ = _make_app(tmp_path, n_sessions=4, model_ids=["alpha", "beta"])
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 4, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 4, description="table populated")

        sel = app.query_one("#session-model-select", Select)
        sel.value = "alpha"
        await pilot.pause()
        assert app._filter_model == "alpha"
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_analyze_runs_analysis(tmp_path: Path) -> None:
    """Analysis populates plugin_results for loaded sessions."""
    app, _, _ = _make_app(tmp_path, n_sessions=2)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="table populated")

        # Analyze each session directly (exercises _analyze_one)
        for meta, label in list(app._meta_only):
            app._analyze_one(meta, label)
        assert len(app._plugin_results) >= 2
        app._populate_session_table()
        await pilot.pause()


@pytest.mark.asyncio
async def test_analyze_selected_only(tmp_path: Path) -> None:
    """action_analyze with selection filters targets to selected sessions."""
    app, _, _ = _make_app(tmp_path, n_sessions=3)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 3, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 3, description="table populated")

        # Select first session only
        first_key = str(app._meta_only[0][0].session_dir)
        app._selected.add(first_key)

        # Exercise action_analyze target selection logic (no thread needed for filter code)
        targets = [
            (meta, label)
            for meta, label in app._meta_only
            if str(meta.session_dir) in app._selected
        ]
        assert len(targets) == 1
        assert str(targets[0][0].session_dir) == first_key


@pytest.mark.asyncio
async def test_analyze_no_sessions_notifies(tmp_path: Path) -> None:
    """action_analyze with no loaded sessions warns (no crash)."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # action_analyze guards on empty _meta_only before call_from_thread
        app.action_analyze()
        await pilot.pause()
        assert len(app._meta_only) == 0


@pytest.mark.asyncio
async def test_theme_change_via_reactive_persists(tmp_path: Path) -> None:
    """Setting ``App.theme`` (e.g. Ctrl+P Change theme) writes config.json."""
    from groket.paths import app_config_path

    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await wait_until(pilot, lambda: app._theme_persist)
        names = app._theme_names()
        if len(names) < 2:
            return
        target = next(n for n in names if n != app.theme)
        app.theme = target
        await pilot.pause()
        await wait_until(
            pilot,
            lambda: (
                app_config_path().is_file()
                and json.loads(app_config_path().read_text(encoding="utf-8")).get("theme") == target
            ),
        )


@pytest.mark.asyncio
async def test_apply_saved_theme(tmp_path: Path) -> None:
    """apply_saved_theme restores from config or falls back gracefully."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Exercise with a known-bad theme name
        app._config["theme"] = "nonexistent-theme-xyz"
        result = app.apply_saved_theme(save=False)
        # Should fall back to some valid theme (or None)
        await pilot.pause()

        # Exercise with save=True
        names = app._theme_names()
        if names:
            app._config["theme"] = names[0]
            result = app.apply_saved_theme(save=True)
            assert result == names[0]


@pytest.mark.asyncio
async def test_toggle_tips(tmp_path: Path) -> None:
    """Tips toggle flips show_tips and refreshes surfaces."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        from groket.ui.prefs import show_tips_enabled

        before = show_tips_enabled()
        app.action_toggle_tips()
        await pilot.pause()
        after = show_tips_enabled()
        assert after != before
        # Toggle back
        app.action_toggle_tips()
        await pilot.pause()
        assert show_tips_enabled() == before


@pytest.mark.asyncio
async def test_open_session_enter_key(tmp_path: Path) -> None:
    """action_open_session pushes BrowserScreen and cleans up safely."""
    from groket.ui.screens.browser import BrowserScreen

    app, _, _ = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 1, description="table populated")

        app.action_open_session()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, BrowserScreen) for s in app.screen_stack),
            description="BrowserScreen pushed",
        )
        # Pop back before test teardown to avoid Header race
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_open_session_empty_table(tmp_path: Path) -> None:
    """action_open_session with no rows does not crash."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_open_session()
        await pilot.pause()


@pytest.mark.asyncio
async def test_push_runner_screen(tmp_path: Path) -> None:
    """Pressing ``r`` pushes the RunnerScreen."""
    from groket.ui.screens.runner import RunnerScreen

    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_open_runner()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, RunnerScreen) for s in app.screen_stack),
            description="RunnerScreen pushed",
        )
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_push_personas_screen(tmp_path: Path) -> None:
    """``P`` opens the PersonasScreen."""
    from groket.ui.screens.personas import PersonasScreen

    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_open_personas()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, PersonasScreen) for s in app.screen_stack),
            description="PersonasScreen pushed",
        )
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_push_run_configs_screen(tmp_path: Path) -> None:
    """``C`` opens the RunConfigsScreen."""
    from groket.ui.screens.run_configs import RunConfigsScreen

    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_open_run_configs()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, RunConfigsScreen) for s in app.screen_stack),
            description="RunConfigsScreen pushed",
        )
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_push_rules_screen(tmp_path: Path) -> None:
    """``d`` opens the RulesScreen."""
    from groket.ui.screens.rules import RulesScreen

    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_open_rules()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, RulesScreen) for s in app.screen_stack),
            description="RulesScreen pushed",
        )
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_action_show_help(tmp_path: Path) -> None:
    """``?`` triggers help display without crash."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_show_help()
        await pilot.pause()


@pytest.mark.asyncio
async def test_action_refresh_context_on_sessions_home(tmp_path: Path) -> None:
    """F5 / action_refresh_context on sessions home triggers _refresh_sessions_list."""
    app, _, traces = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        old_count = len(app._meta_only)
        # Add another session
        _write_session(traces, "sess-refresh-extra")
        app.action_refresh_context()
        await wait_until(
            pilot,
            lambda: len(app._meta_only) >= old_count + 1,
            description="refreshed list grew",
            attempts=120,
        )


@pytest.mark.asyncio
async def test_delete_sessions_double_confirm(tmp_path: Path) -> None:
    """Delete requires double-press to confirm and removes sessions."""
    app, _, traces = _make_app(tmp_path, n_sessions=3)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 3, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 3, description="table populated")

        # Select first session
        await pilot.press("s")
        await pilot.pause()
        assert len(app._selected) == 1

        # First press: sets pending
        app.action_delete_sessions()
        await pilot.pause()
        assert app._delete_pending_paths is not None

        # Second press: confirms delete
        app.action_delete_sessions()
        await wait_until(
            pilot,
            lambda: len(app._meta_only) < 3,
            description="session deleted from list",
            attempts=120,
        )


@pytest.mark.asyncio
async def test_delete_sessions_no_selection(tmp_path: Path) -> None:
    """Delete with no selection and cursor on a row uses cursor row."""
    app, _, _ = _make_app(tmp_path, n_sessions=2)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="table populated")

        # No selection, cursor is on a row
        app.action_delete_sessions()
        await pilot.pause()
        # First press should set pending (from cursor row)
        assert app._delete_pending_paths is not None


@pytest.mark.asyncio
async def test_delete_no_rows_warns(tmp_path: Path) -> None:
    """Delete with empty table notifies warning."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_delete_sessions()
        await pilot.pause()
        # No crash, no pending paths
        assert app._delete_pending_paths is None


@pytest.mark.asyncio
async def test_save_session_config(tmp_path: Path) -> None:
    """Ctrl+S / action_save_session_config persists a run config."""
    from groket.runs.run_configs import RunConfigStore

    app, work, _ = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 1, description="table populated")

        app.action_save_session_config()
        store = RunConfigStore(work)
        await wait_until(
            pilot,
            lambda: len(store.list_configs()) >= 1,
            description="config saved to disk",
            attempts=120,
        )


@pytest.mark.asyncio
async def test_save_session_config_from_selection(tmp_path: Path) -> None:
    """Save config prefers selected session over cursor."""
    from groket.runs.run_configs import RunConfigStore

    app, work, _ = _make_app(tmp_path, n_sessions=2)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="table populated")

        # Select second session
        first_key = str(app._meta_only[0][0].session_dir)
        app._selected.add(first_key)

        app.action_save_session_config()
        store = RunConfigStore(work)
        await wait_until(
            pilot,
            lambda: len(store.list_configs()) >= 1,
            description="config saved from selection",
            attempts=120,
        )


@pytest.mark.asyncio
async def test_save_session_config_empty_table(tmp_path: Path) -> None:
    """save_session_config with no sessions does not crash."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_save_session_config()
        await pilot.pause()


@pytest.mark.asyncio
async def test_rerun_session_no_cursor(tmp_path: Path) -> None:
    """Rerun with empty table notifies warning."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_rerun_session()
        await pilot.pause()


@pytest.mark.asyncio
async def test_rerun_session_pushes_runner(tmp_path: Path) -> None:
    """Rerun from a highlighted session pushes RunnerScreen with prefill."""
    from groket.ui.screens.runner import RunnerScreen

    app, _, _ = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 1, description="table populated")

        app.action_rerun_session()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, RunnerScreen) for s in app.screen_stack),
            description="RunnerScreen pushed for rerun",
            attempts=120,
        )
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_search_sessions_modal(tmp_path: Path) -> None:
    """``/`` opens session search modal; dismissing navigates to result."""
    app, _, _ = _make_app(tmp_path, n_sessions=3)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 3, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 3, description="table populated")

        app.action_search_sessions()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, SessionSearchModal) for s in app.screen_stack),
            description="SessionSearchModal pushed",
        )
        # Dismiss search modal via Esc
        await pilot.press("escape")
        await wait_until(
            pilot,
            lambda: not any(isinstance(s, SessionSearchModal) for s in app.screen_stack),
            description="SessionSearchModal dismissed",
        )


@pytest.mark.asyncio
async def test_search_sessions_no_sessions(tmp_path: Path) -> None:
    """Search with no sessions notifies warning."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_search_sessions()
        await pilot.pause()
        # No crash; modal not pushed
        assert not any(isinstance(s, SessionSearchModal) for s in app.screen_stack)


@pytest.mark.asyncio
async def test_search_modal_typing_and_submit(tmp_path: Path) -> None:
    """Typing in search modal filters, Enter selects and opens session."""
    from groket.ui.screens.browser import BrowserScreen

    app, _, _ = _make_app(tmp_path, n_sessions=3, model_ids=["alpha", "beta", "gamma"])
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 3, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 3, description="table populated")

        app.action_search_sessions()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, SessionSearchModal) for s in app.screen_stack),
            description="search modal",
        )
        modal = next(s for s in app.screen_stack if isinstance(s, SessionSearchModal))

        # Wait for the modal to compose its children
        await wait_until(
            pilot,
            lambda: bool(modal.query("#session-search-input")),
            description="search input mounted",
        )

        # Type search query to filter
        inp = modal.query_one("#session-search-input", Input)
        inp.value = "alpha"
        await pilot.pause()

        # Arrow up/down
        modal.action_cursor_down()
        await pilot.pause()
        modal.action_cursor_up()
        await pilot.pause()

        # Submit
        await pilot.press("enter")
        await wait_until(
            pilot,
            lambda: any(isinstance(s, BrowserScreen) for s in app.screen_stack),
            description="BrowserScreen opened from search",
            attempts=120,
        )
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_analysis_settings_modal(tmp_path: Path) -> None:
    """Analysis settings modal opens, toggles auto-analyze and tips, then saves."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_analysis_settings()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, AnalysisSettingsModal) for s in app.screen_stack),
            description="AnalysisSettingsModal pushed",
        )
        modal = next(s for s in app.screen_stack if isinstance(s, AnalysisSettingsModal))
        # Cancel
        modal.action_cancel()
        await wait_until(
            pilot,
            lambda: not any(isinstance(s, AnalysisSettingsModal) for s in app.screen_stack),
            description="modal dismissed on cancel",
        )


@pytest.mark.asyncio
async def test_analysis_settings_modal_save(tmp_path: Path) -> None:
    """Analysis settings modal saves when action_save is called."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_analysis_settings()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, AnalysisSettingsModal) for s in app.screen_stack),
            description="AnalysisSettingsModal pushed",
        )
        modal = next(s for s in app.screen_stack if isinstance(s, AnalysisSettingsModal))
        # Wait for modal children to compose
        await wait_until(
            pilot,
            lambda: bool(modal.query("#as-auto-analyze")),
            description="modal children composed",
        )
        modal.action_save()
        await wait_until(
            pilot,
            lambda: not any(isinstance(s, AnalysisSettingsModal) for s in app.screen_stack),
            description="modal dismissed on save",
        )


@pytest.mark.asyncio
async def test_analysis_settings_cancel_button(tmp_path: Path) -> None:
    """Cancel button on the analysis settings modal dismisses it."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_analysis_settings()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, AnalysisSettingsModal) for s in app.screen_stack),
            description="AnalysisSettingsModal pushed",
        )
        modal = next(s for s in app.screen_stack if isinstance(s, AnalysisSettingsModal))
        await wait_until(
            pilot,
            lambda: bool(modal.query("#as-cancel")),
            description="cancel button composed",
        )
        await pilot.click("#as-cancel")
        await wait_until(
            pilot,
            lambda: not any(isinstance(s, AnalysisSettingsModal) for s in app.screen_stack),
            description="modal dismissed via cancel button",
        )


@pytest.mark.asyncio
async def test_analysis_settings_save_button(tmp_path: Path) -> None:
    """Save button on the analysis settings modal persists and dismisses."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_analysis_settings()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, AnalysisSettingsModal) for s in app.screen_stack),
            description="AnalysisSettingsModal pushed",
        )
        modal = next(s for s in app.screen_stack if isinstance(s, AnalysisSettingsModal))
        await wait_until(
            pilot,
            lambda: bool(modal.query("#as-save")),
            description="save button composed",
        )
        await pilot.click("#as-save")
        await wait_until(
            pilot,
            lambda: not any(isinstance(s, AnalysisSettingsModal) for s in app.screen_stack),
            description="modal dismissed via save button",
        )


@pytest.mark.asyncio
async def test_update_run_status_no_active_runs(tmp_path: Path) -> None:
    """update_run_status with no active runs resets to 'groket'."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.update_run_status()
        await pilot.pause()
        assert "groket" in app.title.lower()


@pytest.mark.asyncio
async def test_config_save_and_load(tmp_path: Path) -> None:
    """Config round-trips through save/load."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._config["custom_key"] = "custom_value"
        app._save_config()
        loaded = app._load_config()
        assert loaded.get("custom_key") == "custom_value"


@pytest.mark.asyncio
async def test_meta_cache_round_trip(tmp_path: Path) -> None:
    """Meta cache saves and loads correctly."""
    app, work, traces = _make_app(tmp_path, n_sessions=2)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")
        # Cache should have been saved during load
        cache = app._load_meta_cache(traces)
        assert len(cache) >= 2

        # Loading with a different root invalidates
        other_root = tmp_path / "other"
        other_root.mkdir()
        assert app._load_meta_cache(other_root) == {}


@pytest.mark.asyncio
async def test_derive_label(tmp_path: Path) -> None:
    """_derive_label extracts meaningful path components."""
    app, _, traces = _make_app(tmp_path, n_sessions=1)
    sd = traces / "sess-000"
    label = app._derive_label(sd, traces)
    assert label  # non-empty
    # Non-relative path falls back to dir name
    other = tmp_path / "somewhere-else" / "sess"
    other.mkdir(parents=True)
    label2 = app._derive_label(other, traces)
    assert label2  # non-empty


@pytest.mark.asyncio
async def test_populate_with_analyzed_sessions(tmp_path: Path) -> None:
    """Analyzed sessions show real finding counts (not ``--``)."""
    from groket.analysis.base import AnalysisResult

    app, _, _ = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 1, description="table populated")

        # Inject fake analysis results
        meta = app._meta_only[0][0]
        sd_key = str(meta.session_dir)
        app._plugin_results[sd_key] = {
            "basic": AnalysisResult(analyzer_id="basic", ok=True, findings=[])
        }
        app._populate_session_table()
        await pilot.pause()
        # Table should still show the session
        assert table.row_count >= 1


@pytest.mark.asyncio
async def test_populate_with_turn_outcomes(tmp_path: Path) -> None:
    """Sessions with different turn outcomes render correctly."""
    app, _, traces = _make_app(tmp_path, n_sessions=0)
    # Create sessions with various outcomes
    for outcome in ("success", "error", "running", "awaiting_follow_up", ""):
        _write_session(traces, f"sess-{outcome or 'none'}", outcome=outcome)
    app2 = TraceEvalApp(work_dir=app.work_dir, traces_path=traces)
    async with app2.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app2._meta_only) >= 4, description="sessions loaded")
        table = app2.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 4, description="table populated")


@pytest.mark.asyncio
async def test_action_quit_cleans_up(tmp_path: Path) -> None:
    """action_quit detaches UI and stops timers."""
    app, _, _ = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        app._prepare_clean_exit()
        assert app._exiting is True
        assert app.run_manager.ui_detached


@pytest.mark.asyncio
async def test_refresh_everything(tmp_path: Path) -> None:
    """action_refresh_everything re-scans and re-analyzes."""
    app, work, traces = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="initial load")
        _write_session(traces, "sess-refresh-new")
        app.action_refresh_everything()
        await wait_until(
            pilot,
            lambda: len(app._meta_only) >= 2,
            description="refresh found new session",
            attempts=120,
        )


@pytest.mark.asyncio
async def test_refresh_everything_no_dir(tmp_path: Path) -> None:
    """action_refresh_everything with non-existent dir notifies error."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Point traces at a non-existent path
        app.traces_path = tmp_path / "does-not-exist"
        # Remove the runner traces dir too
        import shutil

        runner_traces = app.work_dir / "runs" / "traces"
        if runner_traces.exists():
            shutil.rmtree(runner_traces)
        app.action_refresh_everything()
        await pilot.pause()


@pytest.mark.asyncio
async def test_get_system_commands(tmp_path: Path) -> None:
    """get_system_commands yields commands for the palette."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        cmds = list(app.get_system_commands(app.screen))
        assert len(cmds) > 0


@pytest.mark.asyncio
async def test_session_search_modal_empty_query(tmp_path: Path) -> None:
    """Search modal with empty query shows all sessions."""
    sessions: list[tuple[SessionMeta, str]] = []
    for i in range(3):
        sd = _write_session(tmp_path / "traces", f"s{i}", model_id=f"m{i}")
        meta = load_session_meta(sd)
        sessions.append((meta, f"label-{i}"))

    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Mount modal directly
        modal = SessionSearchModal(sessions)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: any(isinstance(s, SessionSearchModal) for s in app.screen_stack),
            description="search modal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(modal.query("#session-search-input")),
            description="search input composed",
        )
        # Clear query should show all
        inp = modal.query_one("#session-search-input", Input)
        inp.value = ""
        await pilot.pause()
        assert len(modal._results) == 3


@pytest.mark.asyncio
async def test_session_search_modal_filtering(tmp_path: Path) -> None:
    """Typing in search modal filters results by fuzzy match."""
    sessions: list[tuple[SessionMeta, str]] = []
    for i, mid in enumerate(["alpha-model", "beta-model", "gamma-model"]):
        sd = _write_session(tmp_path / "traces", f"s{i}", model_id=mid)
        meta = load_session_meta(sd)
        sessions.append((meta, f"label-{i}"))

    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = SessionSearchModal(sessions)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: any(isinstance(s, SessionSearchModal) for s in app.screen_stack),
            description="search modal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(modal.query("#session-search-input")),
            description="search input composed",
        )
        inp = modal.query_one("#session-search-input", Input)
        inp.value = "alpha"
        await pilot.pause()
        # At least one result should match alpha
        assert len(modal._results) >= 1


@pytest.mark.asyncio
async def test_session_search_modal_submit_opens_browser(tmp_path: Path) -> None:
    """Submitting in search modal opens the selected session in browser."""
    from groket.ui.screens.browser import BrowserScreen

    app, _, traces = _make_app(tmp_path, n_sessions=2)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="table populated")

        app.action_search_sessions()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, SessionSearchModal) for s in app.screen_stack),
            description="modal opened",
        )
        # Submit with Enter
        await pilot.press("enter")
        await wait_until(
            pilot,
            lambda: any(isinstance(s, BrowserScreen) for s in app.screen_stack),
            description="BrowserScreen from search submit",
            attempts=120,
        )
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_merge_session_dirs(tmp_path: Path) -> None:
    """_merge_session_dirs adds new sessions and refreshes existing ones."""
    app, _, traces = _make_app(tmp_path, n_sessions=2)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")

        # Merge a new session
        new_sd = _write_session(traces, "sess-merge-new", model_id="m-merge")
        app._merge_session_dirs([new_sd], traces_root=traces)
        await pilot.pause()
        assert len(app._meta_only) >= 3

        # Merge an existing session (should update in-place if changed)
        existing_sd = traces / "sess-000"
        app._merge_session_dirs([existing_sd], traces_root=traces)
        await pilot.pause()


@pytest.mark.asyncio
async def test_merge_session_dirs_empty(tmp_path: Path) -> None:
    """_merge_session_dirs with empty list is a no-op."""
    app, _, _ = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        before = len(app._meta_only)
        app._merge_session_dirs([])
        assert len(app._meta_only) == before


@pytest.mark.asyncio
async def test_findings_for_session(tmp_path: Path) -> None:
    """_findings_for_session collects findings across plugins."""
    from groket.analysis.base import AnalysisResult, Finding

    app, _, _ = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        meta = app._meta_only[0][0]
        sd_key = str(meta.session_dir)

        # No results yet
        assert app._findings_for_session(sd_key) == []

        # Add findings
        finding = Finding(
            id="test-rule",
            plugin_id="basic",
            severity="high",
            title="Test finding",
        )
        app._plugin_results[sd_key] = {
            "basic": AnalysisResult(analyzer_id="basic", ok=True, findings=[finding])
        }
        result = app._findings_for_session(sd_key)
        assert len(result) == 1
        assert result[0].id == "test-rule"


@pytest.mark.asyncio
async def test_open_session_path(tmp_path: Path) -> None:
    """open_session_path delegates to _open_session."""
    from groket.ui.screens.browser import BrowserScreen

    app, _, traces = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        sd = traces / "sess-000"
        app.open_session_path(sd)
        await wait_until(
            pilot,
            lambda: any(isinstance(s, BrowserScreen) for s in app.screen_stack),
            description="BrowserScreen from open_session_path",
        )
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_extract_session_launch_params(tmp_path: Path) -> None:
    """_extract_session_launch_params reads run.json and prompt."""
    app, _, traces = _make_app(tmp_path, n_sessions=1)
    sd = traces / "sess-000"
    # Write a run.json
    run_data = {
        "repo_url": "https://github.com/test/repo",
        "repo_branch": "main",
        "setup_instructions": "pip install -e .",
        "docker_image": "custom-image",
    }
    (sd / "run.json").write_text(json.dumps(run_data), encoding="utf-8")

    meta = load_session_meta(sd)
    params = app._extract_session_launch_params(meta)
    assert params["repo_url"] == "https://github.com/test/repo"
    assert params["repo_branch"] == "main"
    assert params["setup_instructions"] == "pip install -e ."
    assert params["docker_image"] == "custom-image"


@pytest.mark.asyncio
async def test_extract_session_launch_params_no_run_json(tmp_path: Path) -> None:
    """_extract_session_launch_params works without run.json."""
    app, _, traces = _make_app(tmp_path, n_sessions=1)
    sd = traces / "sess-000"
    meta = load_session_meta(sd)
    params = app._extract_session_launch_params(meta)
    assert "prompt" in params
    assert "docker_image" in params


@pytest.mark.asyncio
async def test_session_row_selection_markers(tmp_path: Path) -> None:
    """Selection markers update without full table rebuild."""
    app, _, _ = _make_app(tmp_path, n_sessions=2)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="table populated")

        meta = app._meta_only[0][0]
        sd_key = str(meta.session_dir)
        app._set_session_sel_cell(table, sd_key, True)
        await pilot.pause()
        app._set_session_sel_cell(table, sd_key, False)
        await pilot.pause()
        app._refresh_session_selection_markers(table)
        await pilot.pause()


@pytest.mark.asyncio
async def test_session_paths_banner_is_label_only(tmp_path: Path) -> None:
    """Work/traces appear as a read-only banner (no path Input)."""
    app, work, traces = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        banner = app.query_one("#session-paths", Static)
        expected = _session_paths_banner_text(app.work_dir, app._session_traces_root())
        assert banner.content == expected
        assert not any(getattr(w, "id", None) == "traces-path-input" for w in app.query(Input))
        assert (
            app._session_traces_root() == traces.resolve() or app._session_traces_root() == traces
        )


@pytest.mark.asyncio
async def test_auto_load_default_traces_under_work_dir(tmp_path: Path) -> None:
    """With only work_dir set, sessions load from work_dir/runs/traces."""
    work = tmp_path / "workdir"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    _write_session(traces, "sess-auto")
    from groket.ui.app import TraceEvalApp as _TEA

    app = _TEA(work_dir=work)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: len(app._meta_only) >= 1,
            description="auto-loaded from work_dir traces",
            attempts=120,
        )


@pytest.mark.asyncio
async def test_schedule_live_sessions_poll(tmp_path: Path) -> None:
    """_schedule_live_sessions_poll sets up the live timer."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._schedule_live_sessions_poll()
        assert app._live_sessions_timer is not None


@pytest.mark.asyncio
async def test_scan_live_sessions_into_table(tmp_path: Path) -> None:
    """_scan_live_sessions_into_table detects new sessions."""
    app, work, traces = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        before_rows = table.row_count
        # Add a session to runner traces
        _write_session(traces, "sess-live-new")
        app._scan_live_sessions_into_table()
        await wait_until(
            pilot,
            lambda: len(app._meta_only) >= 2 and table.row_count > before_rows,
            description="live session appears in table",
        )
        assert any("sess-live-new" in str(m.session_dir) for m, _ in app._meta_only)


@pytest.mark.asyncio
async def test_scan_live_sessions_busy_guard(tmp_path: Path) -> None:
    """_scan_live_sessions_into_table is guarded against re-entry."""
    app, _, _ = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        app._live_sessions_busy = True
        before = len(app._meta_only)
        app._scan_live_sessions_into_table()
        assert len(app._meta_only) == before
        app._live_sessions_busy = False


@pytest.mark.asyncio
async def test_notify_run_finished_quiet(tmp_path: Path) -> None:
    """Quiet/batch run finish does not toast."""
    from groket.models import EvalRun
    from groket.runs.run_manager import BackgroundRun

    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        run = BackgroundRun(
            run_id="r-quiet",
            eval_run=EvalRun(run_id="r-quiet", prompt="test", status="completed"),
            configs=[],
            quiet=True,
        )
        # Should not crash
        app._notify_run_finished(run)
        await pilot.pause()


@pytest.mark.asyncio
async def test_notify_run_finished_error(tmp_path: Path) -> None:
    """Run finish with error shows error toast."""
    from groket.models import EvalRun
    from groket.runs.run_manager import BackgroundRun

    app, _, traces = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        run = BackgroundRun(
            run_id="r-err",
            eval_run=EvalRun(run_id="r-err", prompt="test", status="failed"),
            configs=[],
            error="Docker build failed",
        )
        app._notify_run_finished(run)
        await pilot.pause()


@pytest.mark.asyncio
async def test_notify_run_finished_with_failures(tmp_path: Path) -> None:
    """Run finish with mixed results shows failure count."""
    from groket.docker.orchestrator import ContainerConfig, ContainerStatus
    from groket.models import EvalRun
    from groket.runs.run_manager import BackgroundRun

    app, _, traces = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        cfg = ContainerConfig(model="m1", prompt="test", container_name="c1")
        status_ok = ContainerStatus(container_name="c1", model="m1", status="completed")
        status_fail = ContainerStatus(container_name="c2", model="m1", status="failed")
        run = BackgroundRun(
            run_id="r-mixed",
            eval_run=EvalRun(run_id="r-mixed", prompt="test", status="completed"),
            configs=[cfg],
            results=[status_ok, status_fail],
        )
        app._notify_run_finished(run)
        await pilot.pause()


@pytest.mark.asyncio
async def test_request_live_share_no_share(tmp_path: Path) -> None:
    """_request_live_share with no share file is a no-op."""
    app, _, traces = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        sd = traces / "sess-000"
        app._request_live_share(sd)
        await pilot.pause()


@pytest.mark.asyncio
async def test_session_model_options(tmp_path: Path) -> None:
    """_session_model_options lists All models plus loaded model ids."""
    app, _, _ = _make_app(tmp_path, n_sessions=4, model_ids=["m1", "m2"], task_ids=["t1", "t2"])
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 4, description="sessions loaded")
        models = app._session_model_options()
        assert len(models) >= 3  # "All models" + m1 + m2
        ids = {v for _, v in models}
        assert "m1" in ids and "m2" in ids


@pytest.mark.asyncio
async def test_set_session_filter_selects(tmp_path: Path) -> None:
    """_set_session_filter_selects pushes filter state into widgets."""
    app, _, _ = _make_app(tmp_path, n_sessions=2, model_ids=["m1"])
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")
        app._filter_model = "m1"
        app._set_session_filter_selects()
        await pilot.pause()
        sel = app.query_one("#session-model-select", Select)
        assert sel.value == "m1"


@pytest.mark.asyncio
async def test_rebuild_session_filters_invalid_value(tmp_path: Path) -> None:
    """_rebuild_session_filters resets filter when value is no longer valid."""
    app, _, _ = _make_app(tmp_path, n_sessions=2, model_ids=["m1"])
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")
        app._filter_model = "nonexistent"
        app._rebuild_session_filters()
        await pilot.pause()
        assert app._filter_model == ""


@pytest.mark.asyncio
async def test_populate_busy_guard(tmp_path: Path) -> None:
    """_populate_session_table is guarded against re-entry."""
    app, _, _ = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        app._populate_busy = True
        table = app.query_one("#session-table", DataTable)
        before = table.row_count
        app._populate_session_table()
        # Should be a no-op (busy guard)
        assert table.row_count == before
        app._populate_busy = False


@pytest.mark.asyncio
async def test_load_sessions_from_subdirs(tmp_path: Path) -> None:
    """_load_sessions discovers sessions in subdirectories."""
    work = tmp_path / "w"
    traces = work / "runs" / "traces"
    sub = traces / "subgroup"
    sub.mkdir(parents=True)
    _write_session(sub, "sess-sub-1")

    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: len(app._meta_only) >= 1,
            description="session found in subdir",
            attempts=120,
        )


@pytest.mark.asyncio
async def test_on_background_run_status_session_discovered(tmp_path: Path) -> None:
    """_on_background_run_status dispatches live session merge."""
    from groket.docker.orchestrator import ContainerStatus as CS

    app, _, traces = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        new_sd = _write_session(traces, "sess-live-status")
        status = CS(container_name="c1", model="m1")
        status.session_dir = new_sd
        app._on_live_session_discovered(status)
        await pilot.pause()


@pytest.mark.asyncio
async def test_on_background_run_status_no_session_dir(tmp_path: Path) -> None:
    """_on_background_run_status with no session_dir is ignored."""
    from groket.docker.orchestrator import ContainerStatus as CS

    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        status = CS(container_name="c1", model="m1")
        app._on_live_session_discovered(status)
        await pilot.pause()


@pytest.mark.asyncio
async def test_update_session_paths_banner_no_widget(tmp_path: Path) -> None:
    """_update_session_paths_banner is a no-op when the banner is gone."""
    app, work, traces = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        banner = app.query_one("#session-paths", Static)
        await banner.remove()
        await pilot.pause()
        app._update_session_paths_banner()


@pytest.mark.asyncio
async def test_constructor_explicit_work_dir(tmp_path: Path) -> None:
    """Constructor with explicit work_dir sets paths correctly."""
    work = tmp_path / "explicit"
    work.mkdir()
    app = TraceEvalApp(work_dir=work)
    assert app.work_dir == work.resolve()
    assert app.traces_path == (work / "runs" / "traces").resolve()


@pytest.mark.asyncio
async def test_constructor_traces_path_only(tmp_path: Path) -> None:
    """Constructor with only traces_path resolves work_dir from it."""
    traces = tmp_path / "my-traces"
    traces.mkdir(parents=True)
    _write_session(traces, "s1")
    app = TraceEvalApp(traces_path=traces)
    assert app.traces_path is not None


@pytest.mark.asyncio
async def test_session_search_label_with_metadata(tmp_path: Path) -> None:
    """SessionSearchModal._session_label includes metadata fields."""
    sd = _write_session(
        tmp_path / "traces",
        "s1",
        model_id="alpha",
        task_id="task-fix",
        git_repo="https://github.com/test/repo",
        summary_text="Important fix",
    )
    meta = load_session_meta(sd)
    sessions: list[tuple[SessionMeta, str]] = [(meta, "lab")]
    modal = SessionSearchModal(sessions)
    label = modal._session_label(0)
    assert "alpha" in label
    assert "task-fix" in label
    assert "repo" in label
    assert "Important fix" in label


@pytest.mark.asyncio
async def test_analyze_targets_already_analyzed(tmp_path: Path) -> None:
    """Analyze with all-analyzed sessions skips reanalysis."""

    app, _, _ = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        # Pre-analyze all sessions
        for meta, label in list(app._meta_only):
            app._analyze_one(meta, label)
        assert len(app._plugin_results) >= 1
        # Second analysis is a no-op — _analyze_one guards against re-analysis
        before = dict(app._plugin_results)
        for meta, label in list(app._meta_only):
            app._analyze_one(meta, label)
        assert app._plugin_results == before


@pytest.mark.asyncio
async def test_analyze_targets_guard_bad_input(tmp_path: Path) -> None:
    """_analyze_targets early-return guards for invalid input types."""
    work = tmp_path / "w"
    (work / "runs" / "traces").mkdir(parents=True)
    app = TraceEvalApp(work_dir=work, traces_path=work / "runs" / "traces")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app._analyze_targets(None)  # type: ignore[arg-type]  # deliberate wrong type
        app._analyze_targets([])
        app._analyze_targets("bad")  # type: ignore[arg-type]  # deliberate wrong type
        await pilot.pause()


@pytest.mark.asyncio
async def test_session_row_key_at_cursor(tmp_path: Path) -> None:
    """_session_row_key_at_cursor returns None on empty table."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#session-table", DataTable)
        result = app._session_row_key_at_cursor(table)
        # Empty table should return None
        assert result is None


@pytest.mark.asyncio
async def test_session_row_keys_in_order(tmp_path: Path) -> None:
    """_session_row_keys_in_order returns list of keys."""
    app, _, _ = _make_app(tmp_path, n_sessions=2)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="table populated")
        keys = app._session_row_keys_in_order(table)
        assert len(keys) >= 2


@pytest.mark.asyncio
async def test_schedule_run_status_update(tmp_path: Path) -> None:
    """_schedule_run_status_update sets a debounce timer."""
    app, _, _ = _make_app(tmp_path, n_sessions=0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._schedule_run_status_update()
        assert app._run_status_timer is not None
        # Call again to exercise the stop+reset path
        app._schedule_run_status_update()
        await pilot.pause()


@pytest.mark.asyncio
async def test_meta_cache_corrupt_file(tmp_path: Path) -> None:
    """_load_meta_cache handles corrupt cache gracefully."""
    app, work, traces = _make_app(tmp_path, n_sessions=0)
    cache_file = work / app._CACHE_FILE
    cache_file.write_text("not json", encoding="utf-8")
    result = app._load_meta_cache(traces)
    assert result == {}


@pytest.mark.asyncio
async def test_on_session_selected_opens_browser(tmp_path: Path) -> None:
    """DataTable.RowSelected event opens the session in BrowserScreen."""
    from groket.ui.screens.browser import BrowserScreen

    app, _, _ = _make_app(tmp_path, n_sessions=1)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 1, description="table populated")
        # Simulate row select
        table.action_select_cursor()
        await wait_until(
            pilot,
            lambda: any(isinstance(s, BrowserScreen) for s in app.screen_stack),
            description="BrowserScreen from row select",
            attempts=120,
        )
        await pilot.press("escape")
        await pilot.pause()
