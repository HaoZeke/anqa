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
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = []
    screen.meta = None
    screen._live_refresh_busy = False
    screen._live_refresh_pending = False
    screen._last_trace_mtime = None
    screen._trace_watch = None
    screen._live_refresh_timer = None
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
    screen._live_refresh_from_fs = lambda: calls.append("tick")  # type: ignore[method-assign]
    screen._live_refresh_worker_done()
    assert screen._live_refresh_busy is False
    assert calls == ["tick"]
    assert is_inflight(KIND_REFRESH, sd) is False
