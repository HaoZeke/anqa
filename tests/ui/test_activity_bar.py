"""Activity bar line builder and app counter reads."""

from __future__ import annotations

from types import SimpleNamespace

from anqa.ui.widgets.activity_bar import (
    activity_counters_from_app,
    activity_is_busy,
    activity_line_signature,
    build_activity_line,
    stabilize_activity_counts,
)


def test_build_activity_line_is_catalog_size():
    text = build_activity_line(sessions_loaded=5)
    plain = text.plain
    assert "Sessions 5" in plain
    assert "Running" not in plain
    assert "Building" not in plain
    assert "Awaiting" not in plain


def test_activity_is_busy_never():
    assert not activity_is_busy({"sessions": 3})
    assert not activity_is_busy({"running": 2, "building": 1})


def test_activity_counters_are_catalog_size():
    meta_running = SimpleNamespace(list_status_label=lambda: "running")
    meta_done = SimpleNamespace(list_status_label=lambda: "complete")
    app = SimpleNamespace(_meta_only=[(meta_running, "x"), (meta_done, "y")])
    counts = activity_counters_from_app(app)
    assert counts == {"sessions": 2}


def test_activity_counters_empty_catalog():
    app = SimpleNamespace(_meta_only=[])
    counts = activity_counters_from_app(app)
    assert counts == {"sessions": 0}


def test_stabilize_activity_counts_is_catalog_size():
    held, holds = stabilize_activity_counts(
        {"sessions": 4, "running": 9},
        prev={"sessions": 3},
        hold_until={},
        now=100.0,
    )
    assert held == {"sessions": 4}
    assert holds == {}


def test_activity_line_signature_is_session_count():
    assert activity_line_signature({"sessions": 2, "running": 9}) == (2,)
