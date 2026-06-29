"""One-line activity strip: Runs · Analysis · Sessions (top right)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from ..i18n import t

logger = logging.getLogger(__name__)


class _RunManagerView(Protocol):
    @property
    def active_count(self) -> int: ...


class _AppActivityView(Protocol):
    run_manager: _RunManagerView
    _analysis_jobs_active: int
    _meta_only: list[object]


def build_activity_line(
    *,
    runs_active: int,
    analyze_active: int,
    sessions_loaded: int,
) -> Text:
    """Build a right-aligned strip: Runs N · Analysis N · Sessions N."""
    line = Text()
    line.append(t("activity-runs", n=runs_active), style="bold green" if runs_active else "dim")
    line.append("  ·  ", style="dim")
    line.append(
        t("activity-analysis", n=analyze_active),
        style="bold yellow" if analyze_active else "dim",
    )
    line.append("  ·  ", style="dim")
    line.append(t("activity-sessions", n=sessions_loaded), style="dim")
    return line


if TYPE_CHECKING:
    from textual.app import App


def activity_counters_from_app(app: App) -> tuple[int, int, int]:
    runs_n = 0
    rm = getattr(app, "run_manager", None)
    if rm is not None:
        runs_n = int(getattr(rm, "active_count", 0) or 0)
    analyze_n = int(getattr(app, "_analysis_jobs_active", 0) or 0)
    meta_only = getattr(app, "_meta_only", None) or []
    sessions_n = len(meta_only) if hasattr(meta_only, "__len__") else 0
    return (runs_n, analyze_n, sessions_n)


class ActivityBar(Static):
    """Docked under Header; content aligned to the top-right."""

    DEFAULT_CSS = """
    ActivityBar {
        dock: top;
        height: 1;
        width: 100%;
        background: $boost;
        color: $text;
        padding: 0 1;
        content-align: right middle;
        text-align: right;
    }
    """

    def __init__(self) -> None:
        super().__init__("", id="activity-bar")
        self._timer: Timer | None = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(1.0, self.refresh_activity)
        self.refresh_activity()

    def on_unmount(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is not None:
            timer.stop()

    def refresh_activity(self) -> None:
        try:
            runs, analyze, sessions = activity_counters_from_app(self.app)
            self.update(
                build_activity_line(
                    runs_active=runs,
                    analyze_active=analyze,
                    sessions_loaded=sessions,
                )
            )
        except Exception:
            logger.exception("activity bar refresh failed")
