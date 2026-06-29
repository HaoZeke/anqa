"""One-line activity strip (top right): live sessions, runs, optional analysis, library."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from ..i18n import t
from ..styles import status_rich_style

logger = logging.getLogger(__name__)


class _RunManagerView(Protocol):
    @property
    def active_count(self) -> int: ...

    @property
    def active_session_count(self) -> int: ...


def build_activity_line(
    *,
    live_sessions: int,
    runs_active: int,
    analyze_active: int,
    sessions_loaded: int,
) -> Text:
    """Right-aligned strip: Live · Runs · [Analysis] · Lib.

    * **Live** — running eval sessions (containers); yellow when > 0.
    * **Runs** — active launches (one run may own several sessions); yellow when > 0.
    * **Analysis** — only when background analyze jobs are in flight (cyan).
    * **Lib** — sessions loaded in the home list (always dim; catalog size).
    """
    idle = status_rich_style("idle")
    line = Text()
    live_style = status_rich_style("running" if live_sessions else "idle")
    runs_style = status_rich_style("running" if runs_active else "idle")
    line.append(t("activity-live", n=live_sessions), style=live_style)
    line.append("  ·  ", style=idle)
    line.append(t("activity-runs", n=runs_active), style=runs_style)
    if analyze_active > 0:
        line.append("  ·  ", style=idle)
        line.append(
            t("activity-analysis", n=analyze_active),
            style=status_rich_style("building"),
        )
    line.append("  ·  ", style=idle)
    line.append(t("activity-lib", n=sessions_loaded), style=idle)
    return line


if TYPE_CHECKING:
    from textual.app import App


def activity_counters_from_app(app: App) -> tuple[int, int, int, int]:
    """Return ``(live_sessions, runs, analysis, lib_sessions)``."""
    runs_n = 0
    live_n = 0
    rm = getattr(app, "run_manager", None)
    if rm is not None:
        runs_n = int(getattr(rm, "active_count", 0) or 0)
        live_n = int(getattr(rm, "active_session_count", 0) or 0)
    # Also count loaded metas that are still in-progress / awaiting (no container yet).
    meta_only = getattr(app, "_meta_only", None) or []
    meta_live = 0
    for item in meta_only:
        meta = item[0] if isinstance(item, tuple) and item else item
        label_fn = getattr(meta, "list_status_label", None)
        if callable(label_fn):
            st = label_fn()
            if st in ("running", "awaiting"):
                meta_live += 1
        elif getattr(meta, "turn_in_progress", False):
            meta_live += 1
    live_n = max(live_n, meta_live)
    analyze_n = int(getattr(app, "_analysis_jobs_active", 0) or 0)
    sessions_n = len(meta_only) if hasattr(meta_only, "__len__") else 0
    return (live_n, runs_n, analyze_n, sessions_n)


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
            live, runs, analyze, lib = activity_counters_from_app(self.app)
            self.update(
                build_activity_line(
                    live_sessions=live,
                    runs_active=runs,
                    analyze_active=analyze,
                    sessions_loaded=lib,
                )
            )
        except Exception:
            logger.exception("activity bar refresh failed")
