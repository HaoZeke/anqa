"""TraceEvalApp import, construction, populate, and session-loading tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.parser import find_sessions, load_session_meta
from textual.widgets import DataTable

from .pilot_helpers import wait_until


def _write_minimal_session(traces_root: Path, session_id: str = "sess-launch-001") -> Path:
    """Create a tiny session dir that :func:`find_sessions` / meta load accept."""
    sd = traces_root / session_id
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": session_id, "cwd": "/workspace"},
                "session_summary": "Launch smoke session",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "test-model",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "turn_ended",
                "ts": "2026-06-25T00:01:00Z",
                "outcome": "success",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return sd


def test_trace_eval_app_importable():
    """Main app module must import (catches broken Textual / package imports)."""
    from groket.ui import app as app_mod
    from groket.ui.app import TraceEvalApp
    from textual.app import App, ComposeResult, SystemCommand
    from textual.timer import Timer

    assert issubclass(TraceEvalApp, App)
    # Guard against the regression that imported ComposeResult from textual.timer.
    assert ComposeResult is not None
    assert SystemCommand is not None
    assert Timer is not None
    assert hasattr(app_mod, "TraceEvalApp")


def test_trace_eval_app_constructs(tmp_path: Path):
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    assert app.work_dir == work.resolve()
    assert app.traces_path == traces.resolve()


def test_populate_session_table_shows_unanalyzed_rows(tmp_path: Path):
    """Unanalyzed sessions must still render (findings column ``--``).

    Regression: ``finding_count`` was unset for unanalyzed rows; with a broad
    ``except Exception`` around ``add_row`` the table looked empty. Populate
    must not swallow programming errors — they should fail the test.
    """
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sd = _write_minimal_session(traces)
    meta = load_session_meta(sd)
    assert meta is not None

    app = TraceEvalApp(work_dir=work, traces_path=traces)
    app._meta_only = [(meta, "lab")]
    app._plugin_results = {}
    app._selected = set()
    app._filter_model = ""
    app._populate_busy = False

    # Drive populate without full Textual run: install a minimal DataTable host.
    # Prefer run_test for realism (below); this unit path asserts the row logic.
    rows_added: list[tuple] = []

    class _FakeTable:
        def clear(self) -> None:
            rows_added.clear()

        def add_row(self, *cells, key=None):
            rows_added.append((cells, key))

        @property
        def cursor_coordinate(self):
            return None

    class _FakeStatic:
        def update(self, _content) -> None:
            return None

    class _FakeApp(TraceEvalApp):
        def query_one(self, selector, expect_type=None):  # type: ignore[no-untyped-def]  # test stub
            if selector == "#session-table":
                return _FakeTable()
            if selector == "#session-summary":
                return _FakeStatic()
            raise KeyError(selector)

    host = _FakeApp(work_dir=work, traces_path=traces)
    host._meta_only = [(meta, "lab")]
    host._plugin_results = {}
    host._selected = set()
    host._filter_model = ""
    host._populate_busy = False
    # Avoid focus side-effects on a non-mounted widget tree.
    host._populate_session_table = (  # type: ignore[method-assign]  # test stub
        lambda **kw: host._populate_session_table_inner(**kw)
    )
    # Still exercise the real inner path (summary update included).
    host._update_summary_lazy = lambda *a, **k: None  # type: ignore[method-assign]  # test stub
    host._populate_session_table_inner()
    assert len(rows_added) == 1
    cells, key = rows_added[0]
    # findings column is index 8 (after sel, id, model, task, title, turn, dur, events)
    assert str(cells[8]) == "--"
    assert key == str(meta.session_dir)


@pytest.mark.asyncio
async def test_app_launch_lists_sessions(tmp_path: Path):
    """Full Textual pilot: mount TraceEvalApp and expect session rows."""
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_minimal_session(traces, "sess-a")
    _write_minimal_session(traces, "sess-b")
    assert len(find_sessions(traces)) >= 2

    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test() as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="session rows populated")


@pytest.mark.asyncio
async def test_app_launch_empty_traces_notifies(tmp_path: Path):
    """Empty traces dir should not crash; table stays empty."""
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)

    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test() as pilot:
        # Worker thread runs and finds zero sessions; wait for it to finish.
        await wait_until(
            pilot,
            lambda: hasattr(app, "_meta_only"),
            description="worker finished (empty traces)",
        )
        table = app.query_one("#session-table", DataTable)
        assert table.row_count == 0
