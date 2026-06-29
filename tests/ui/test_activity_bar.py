"""Activity bar line builder and app counter reads."""

from __future__ import annotations

from types import SimpleNamespace

from groket.ui.widgets.activity_bar import activity_counters_from_app, build_activity_line


def test_build_activity_line_idle():
    text = build_activity_line(
        live_sessions=0, runs_active=0, analyze_active=0, sessions_loaded=5
    )
    plain = text.plain
    assert "Live" in plain
    assert "Runs" in plain
    assert "Lib" in plain
    assert "Analysis" not in plain  # hidden when zero


def test_build_activity_line_busy():
    from groket.ui.styles import status_rich_style

    text = build_activity_line(
        live_sessions=3, runs_active=1, analyze_active=2, sessions_loaded=10
    )
    plain = text.plain
    assert "Live 3" in plain or "3" in plain
    assert "Runs 1" in plain or "Runs" in plain
    assert "Analysis 2" in plain or "Analysis" in plain
    assert "Lib 10" in plain or "10" in plain
    assert status_rich_style("running") == "bold yellow"
    styles = {str(span.style) for span in text.spans}
    assert any("yellow" in s for s in styles)


def test_activity_counters_from_app():
    meta_running = SimpleNamespace(
        list_status_label=lambda: "running", turn_in_progress=True, session_dir="a"
    )
    meta_done = SimpleNamespace(
        list_status_label=lambda: "complete", turn_in_progress=False, session_dir="b"
    )
    app = SimpleNamespace(
        run_manager=SimpleNamespace(active_count=1, active_session_count=3),
        _analysis_jobs_active=1,
        _meta_only=[(meta_running, "x"), (meta_done, "y")],
    )
    live, runs, analyze, lib = activity_counters_from_app(app)
    assert runs == 1
    assert live == 3  # from run_manager sessions
    assert analyze == 1
    assert lib == 2


def test_activity_counters_prefers_meta_live_when_higher():
    meta_running = SimpleNamespace(list_status_label=lambda: "running")
    app = SimpleNamespace(
        run_manager=SimpleNamespace(active_count=0, active_session_count=0),
        _analysis_jobs_active=0,
        _meta_only=[(meta_running, "x")],
    )
    live, runs, analyze, lib = activity_counters_from_app(app)
    assert live == 1
    assert runs == 0
    assert lib == 1
