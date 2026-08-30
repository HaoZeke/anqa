"""One-line activity strip (top right): catalog size."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from ..i18n import t
from ..styles import status_rich_style, theme_is_light

logger = logging.getLogger(__name__)


def build_activity_line(*, sessions_loaded: int = 0, light: bool = False) -> Text:
    """Catalog size only."""
    line = Text()
    line.append(
        t("activity-sessions", n=sessions_loaded),
        style=status_rich_style("idle", light=light),
    )
    return line


def stabilize_activity_counts(
    raw: dict[str, int],
    *,
    prev: dict[str, int] | None = None,
    hold_until: dict[str, float] | None = None,
    now: float | None = None,
    hold_s: float = 0.0,
) -> tuple[dict[str, int], dict[str, float]]:
    """Pass through the catalog size (no lifecycle hold)."""
    _ = prev, hold_until, now, hold_s
    n = int(raw.get("sessions", 0) or 0)
    return {"sessions": n}, {}


def activity_line_signature(counts: dict[str, int]) -> tuple[int, ...]:
    """Identity for the strip."""
    return (int(counts.get("sessions", 0) or 0),)


if TYPE_CHECKING:
    from textual.app import App


def activity_counters_from_app(app: App) -> dict[str, int]:
    """Catalog size for the activity bar."""
    meta_only = getattr(app, "_meta_only", None) or []
    n = len(meta_only) if hasattr(meta_only, "__len__") else 0
    return {"sessions": n}


def activity_is_busy(counts: dict[str, int]) -> bool:
    """The strip has no short busy phase."""
    _ = counts
    return False


class ActivityBar(Static):
    """Right side of the one-row chrome: catalog size."""

    DEFAULT_CSS = """
    ActivityBar {
        dock: none;
        height: 1;
        width: 1fr;
        background: $panel;
        color: $text;
        padding: 0 1;
        content-align: right middle;
        text-align: right;
    }
    """

    def __init__(self) -> None:
        super().__init__("", id="activity-bar")
        self._timer: Timer | None = None
        self._last_signature: tuple[int, ...] | None = None

    def on_mount(self) -> None:
        from ...constants import ACTIVITY_BAR_INTERVAL

        self._timer = self.set_interval(ACTIVITY_BAR_INTERVAL, self.refresh_activity)
        self.refresh_activity()

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def refresh_activity(self) -> None:
        try:
            counts = activity_counters_from_app(self.app)
            sig = activity_line_signature(counts)
            if sig == self._last_signature:
                return
            self._last_signature = sig
            self.update(
                build_activity_line(
                    sessions_loaded=counts["sessions"],
                    light=theme_is_light(str(self.app.theme or "")),
                )
            )
        except Exception:
            logger.exception("activity bar refresh failed")
