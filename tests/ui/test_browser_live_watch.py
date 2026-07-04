"""Browser live-refresh watch root and inflight contracts."""

from __future__ import annotations

from pathlib import Path

from groket.session_inflight import KIND_REFRESH, clear, try_begin
from groket.ui.screens.browser import BrowserScreen


def setup_function() -> None:
    clear(KIND_REFRESH)


def teardown_function() -> None:
    clear(KIND_REFRESH)


def test_live_watch_root_uses_traces_volume(tmp_path: Path) -> None:
    """Watch root is the traces volume when turn gates live there."""
    vol = tmp_path / "traces" / "ctr"
    vol.mkdir(parents=True)
    (vol / ".groket-turn").mkdir()
    sess = vol / "%2Fworkspace" / "019f-sess"
    sess.mkdir(parents=True)
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sess
    assert screen._live_watch_root() == vol.resolve() or screen._live_watch_root() == vol


def test_live_watch_root_falls_back_to_session_dir(tmp_path: Path) -> None:
    """When no volume is found, watch the session directory itself."""
    sess = tmp_path / "orphan-session"
    sess.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sess
    root = screen._live_watch_root()
    assert root == sess or root == sess.resolve()


def test_live_refresh_worker_done_clears_busy_and_runs_pending(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_busy = True
    screen._live_refresh_pending = True
    from groket.session_inflight import request_rerun

    request_rerun(KIND_REFRESH, sd)
    calls: list[str] = []
    screen._live_refresh_from_fs = (  # type: ignore[method-assign]
        lambda **kwargs: calls.append(("tick", bool(kwargs.get("heartbeat"))))
    )
    screen._light_refresh_heartbeat = True
    screen._live_refresh_worker_done()
    assert screen._live_refresh_busy is False
    assert screen._live_refresh_pending is False
    assert screen._light_refresh_heartbeat is False
    assert calls == [("tick", True)]


def test_live_refresh_from_fs_sets_pending_when_busy(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = []
    screen.meta = None
    screen._live_refresh_busy = False
    screen._live_refresh_pending = False
    screen._session_is_pending = lambda: False  # type: ignore[method-assign]
    screen._session_needs_live_timeline = lambda: True  # type: ignore[method-assign]
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_from_fs()
    assert screen._live_refresh_pending is True


def test_live_watch_root_prefers_volume_for_uuid_session_layout(tmp_path: Path) -> None:
    """Typical eval layout without gates yet still watches the container volume."""
    vol = tmp_path / "traces" / "ctr"
    sess = vol / "%2Fworkspace" / "019fabc-session-id"
    sess.mkdir(parents=True)
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sess
    assert screen._live_watch_root().resolve() == vol.resolve()


def test_schedule_live_refresh_arms_heartbeat(tmp_path: Path) -> None:
    from groket.constants import LIVE_POLL_HEARTBEAT_INTERVAL
    from groket.session.context_samples import ContextSampleStore

    sd = tmp_path / "019f-sess"
    sd.mkdir()
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = []
    screen.meta = None
    screen._live_refresh_busy = False
    screen._live_refresh_pending = False
    screen._live_refresh_timer = None
    screen._live_heartbeat_timer = None
    screen._trace_watch = None
    screen._context_samples = ContextSampleStore()
    screen._session_is_pending = lambda: False  # type: ignore[method-assign]
    screen._session_needs_live_timeline = lambda: True  # type: ignore[method-assign]
    screen._refresh_session_pending_bar = lambda: None  # type: ignore[method-assign]
    timers: list[float] = []

    class _T:
        def stop(self) -> None:
            return None

    def _set_interval(interval, callback):
        timers.append(float(interval))
        return _T()

    screen.set_interval = _set_interval  # type: ignore[method-assign]
    screen._schedule_live_refresh()
    assert LIVE_POLL_HEARTBEAT_INTERVAL in timers
    assert screen._live_heartbeat_timer is not None


def test_live_refresh_heartbeat_passes_flag(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    flags: list[bool] = []
    screen._live_refresh_from_fs = (  # type: ignore[method-assign]
        lambda **kwargs: flags.append(bool(kwargs.get("heartbeat")))
    )
    screen._live_refresh_heartbeat()
    assert flags == [True]


def test_record_context_sample_and_turn_index(tmp_path: Path) -> None:
    from groket.models import SessionMeta, TraceEvent
    from groket.session.context_samples import ContextSampleStore

    sd = tmp_path / "s"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = [
        TraceEvent(
            index=0, timestamp=1.0, event_type="turn_started", content="Turn started turn_number=0"
        ),
        TraceEvent(index=1, timestamp=2.0, event_type="user_message_chunk", content="hi"),
    ]
    screen.meta = SessionMeta(
        session_id="s",
        session_dir=sd,
        context_window_usage_pct=35,
        context_tokens_used=178996,
        context_window_tokens=500000,
    )
    screen._context_samples = ContextSampleStore()
    assert screen._current_turn_index() == 0
    assert screen._record_context_sample() is True
    assert screen._record_context_sample() is False
    assert "35%" in screen._context_samples.compact_for_turn(0)


def test_signals_mtime_missing_and_oserror(tmp_path: Path, monkeypatch) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    assert screen._signals_mtime() == 0.0
    sig = sd / "signals.json"
    sig.write_text("{}", encoding="utf-8")
    assert screen._signals_mtime() > 0.0

    class _BoomStat:
        def __init__(self, *a, **k):
            raise OSError("nope")

    monkeypatch.setattr(Path, "stat", _BoomStat)
    assert screen._signals_mtime() == 0.0
