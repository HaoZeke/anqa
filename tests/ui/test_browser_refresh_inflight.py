"""Browser live refresh respects per-session inflight lock."""

from __future__ import annotations

from pathlib import Path

from groket.job_pools import get_live_refresh_pool
from groket.session_inflight import (
    KIND_REFRESH,
    clear,
    is_inflight,
    try_begin,
)
from groket.ui.screens.browser import BrowserScreen


def setup_function() -> None:
    clear(KIND_REFRESH)


def teardown_function() -> None:
    clear(KIND_REFRESH)


def _screen(sd: Path) -> BrowserScreen:
    from groket.session.context_samples import ContextSampleStore

    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = []
    screen.meta = None
    screen._live_refresh_busy = False
    screen._live_refresh_pending = False
    screen._last_trace_mtime = None
    screen._last_signals_mtime = None
    screen._trace_watch = None
    screen._live_refresh_timer = None
    screen._live_heartbeat_timer = None
    screen._context_samples = ContextSampleStore()
    screen._light_refresh_heartbeat = False
    return screen


def test_live_refresh_skips_second_enqueue(tmp_path: Path) -> None:
    sd = tmp_path / "019f-sess"
    sd.mkdir()
    screen = _screen(sd)
    screen._session_is_pending = lambda: False  # type: ignore[method-assign]
    screen._session_needs_live_timeline = lambda: True  # type: ignore[method-assign]
    submitted: list[str] = []
    pool = get_live_refresh_pool()
    real = pool.submit
    pool.submit = lambda label, fn: submitted.append(label)  # type: ignore[method-assign]
    try:
        assert try_begin(KIND_REFRESH, sd) is True
        screen._live_refresh_from_fs()
        assert submitted == []
        assert screen._live_refresh_pending is True
        assert is_inflight(KIND_REFRESH, sd) is True
        screen._live_refresh_from_fs()
        assert submitted == []
    finally:
        pool.submit = real  # type: ignore[method-assign]


def test_live_refresh_enqueues_when_lock_free(tmp_path: Path) -> None:
    sd = tmp_path / "019f-sess"
    sd.mkdir()
    screen = _screen(sd)
    screen._session_is_pending = lambda: False  # type: ignore[method-assign]
    screen._session_needs_live_timeline = lambda: True  # type: ignore[method-assign]
    submitted: list[str] = []
    pool = get_live_refresh_pool()
    real = pool.submit
    pool.submit = lambda label, fn: submitted.append(label)  # type: ignore[method-assign]
    try:
        screen._live_refresh_from_fs()
        assert submitted == [f"refresh {sd.name}"]
        assert screen._live_refresh_busy is True
        assert is_inflight(KIND_REFRESH, sd) is True
    finally:
        pool.submit = real  # type: ignore[method-assign]


def test_worker_done_runs_coalesced_follow_up(tmp_path: Path) -> None:
    sd = tmp_path / "019f-sess"
    sd.mkdir()
    screen = _screen(sd)
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_busy = True
    from groket.session_inflight import request_rerun

    request_rerun(KIND_REFRESH, sd)
    calls: list[str] = []
    screen._live_refresh_from_fs = (  # type: ignore[method-assign]
        lambda **kwargs: calls.append(("tick", bool(kwargs.get("heartbeat"))))
    )
    screen._live_refresh_worker_done()
    assert screen._live_refresh_busy is False
    assert calls == [("tick", False)]
    assert is_inflight(KIND_REFRESH, sd) is False


def test_live_refresh_heartbeat_coalesces_flag(tmp_path: Path) -> None:
    sd = tmp_path / "019f-sess"
    sd.mkdir()
    screen = _screen(sd)
    screen._session_is_pending = lambda: False  # type: ignore[method-assign]
    screen._session_needs_live_timeline = lambda: True  # type: ignore[method-assign]
    submitted: list[str] = []
    pool = get_live_refresh_pool()
    real = pool.submit
    pool.submit = lambda label, fn: submitted.append(label)  # type: ignore[method-assign]
    try:
        screen._live_refresh_from_fs()
        assert submitted == [f"refresh {sd.name}"]
        screen._live_refresh_from_fs(heartbeat=True)
        assert screen._live_refresh_pending is True
        assert screen._light_refresh_heartbeat is True
    finally:
        pool.submit = real  # type: ignore[method-assign]


def test_load_data_light_always_reloads_meta(tmp_path: Path, monkeypatch) -> None:
    """Heartbeat/FS refresh re-reads signals even when timeline mtime is unchanged."""
    import groket.parser as parser_mod
    from groket.models import SessionMeta
    from groket.ui.screens import browser as browser_mod

    sd = tmp_path / "019f-sess"
    sd.mkdir()
    screen = _screen(sd)
    screen.timeline = [object()]  # non-empty so unchanged path skips parse
    screen._last_trace_mtime = 1.0
    screen._last_signals_mtime = 1.0
    screen.meta = SessionMeta(
        session_id="s",
        session_dir=sd,
        context_window_usage_pct=10,
        context_tokens_used=1,
        context_window_tokens=500000,
    )
    calls: list[str] = []

    monkeypatch.setattr(parser_mod, "session_trace_mtime", lambda _p: 1.0)
    monkeypatch.setattr(
        browser_mod,
        "load_session_meta",
        lambda _p, include_timeline_count=False: SessionMeta(
            session_id="s",
            session_dir=sd,
            context_window_usage_pct=35,
            context_tokens_used=178996,
            context_window_tokens=500000,
        ),
    )
    monkeypatch.setattr(
        browser_mod,
        "parse_timeline",
        lambda _p: calls.append("parse") or [],
    )

    def _call_ui(_app, cb, *a, **k):
        name = getattr(cb, "__name__", str(cb))
        calls.append(name)
        if name == "_live_refresh_worker_done":
            return cb(*a, **k)
        return None

    monkeypatch.setattr(browser_mod, "call_ui", _call_ui)
    screen._signals_mtime = lambda: 1.0  # type: ignore[method-assign]
    screen._rebuild_indices = lambda: calls.append("rebuild")  # type: ignore[method-assign]
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_busy = True
    screen._load_data_light_job()
    assert "parse" not in calls
    assert "rebuild" not in calls
    assert screen.meta is not None
    assert screen.meta.context_window_usage_pct == 35
    assert screen.meta.num_events == 1
    assert "_populate_ui_light" in calls
    assert "_live_refresh_worker_done" in calls
    assert is_inflight(KIND_REFRESH, sd) is False
