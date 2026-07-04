"""Browser live-refresh watch root and busy-flag contracts."""

from __future__ import annotations

from pathlib import Path

from groket.ui.screens.browser import BrowserScreen


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


def test_live_refresh_worker_done_clears_busy_and_runs_pending() -> None:
    screen = BrowserScreen.__new__(BrowserScreen)
    screen._live_refresh_busy = True
    screen._live_refresh_pending = True
    calls: list[str] = []
    screen._live_refresh_from_fs = lambda: calls.append("tick")  # type: ignore[method-assign]
    screen._live_refresh_worker_done()
    assert screen._live_refresh_busy is False
    assert screen._live_refresh_pending is False
    assert calls == ["tick"]


def test_live_refresh_from_fs_sets_pending_when_busy() -> None:
    screen = BrowserScreen.__new__(BrowserScreen)
    screen._live_refresh_busy = True
    screen._live_refresh_pending = False
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
