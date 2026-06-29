"""Activity bar line builder and app counter reads."""

from __future__ import annotations

from types import SimpleNamespace

from groket.ui.widgets.activity_bar import activity_counters_from_app, build_activity_line


def test_build_activity_line_idle():
    text = build_activity_line(runs_active=0, analyze_active=0, sessions_loaded=0)
    plain = text.plain
    assert "Runs" in plain
    assert "Analysis" in plain
    assert "Sessions" in plain
    assert "self-test" not in plain.lower()


def test_build_activity_line_busy():
    text = build_activity_line(runs_active=2, analyze_active=3, sessions_loaded=10)
    plain = text.plain
    assert "Runs 2" in plain or "2" in plain
    assert "Analysis 3" in plain or "3" in plain
    assert "Sessions 10" in plain or "10" in plain
    assert "FAIL" not in plain
    assert "batches" not in plain.lower()


def test_activity_counters_from_app():
    app = SimpleNamespace(
        run_manager=SimpleNamespace(active_count=2, active_batch_ids=["b1"]),
        _analysis_jobs_active=1,
        _meta_only=[object(), object()],
        _plugin_results={"a": object()},
        _self_test_summary="self-test PASS",
    )
    runs, analyze, sessions = activity_counters_from_app(app)
    assert runs == 2
    assert analyze == 1
    assert sessions == 2
