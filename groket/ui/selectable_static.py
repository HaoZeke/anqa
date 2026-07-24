"""Static content that supports Textual text selection and plain-text copy.

Detail panes often hold Markdown / Syntax / Group renderables. Textual's
default :meth:`Widget.get_selection` only extracts from ``Text`` / ``Content``,
so mouse selection can fail for rich bodies. This widget keeps a plain-text
cache (materialized at the pane width) for clipboard yank and selection
fallback, while **displaying** the original renderable so layout reflows and
content is not pre-wrapped/clipped at a stale width.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.protocol import is_renderable
from rich.text import Text
from textual import events
from textual.content import Content
from textual.selection import Selection
from textual.visual import VisualType
from textual.widgets import Static


def materialize_selectable(
    renderable: object, *, width: int = 100
) -> tuple[str | Text | Content, str]:
    """Turn a display renderable into selectable Text (or str) + plain cache.

    Used for the plain-text cache / selection fallback — not required for
    on-screen display (prefer the original Markdown/Syntax/Group there).

    :param renderable: String, Rich text, Markdown/Syntax/Group, or similar.
    :param width: Wrap width for plain extraction layout.
    :returns: ``(visual_text, plain_text)``.
    """
    if renderable is None:
        return "", ""
    if isinstance(renderable, str):
        return renderable, renderable
    if isinstance(renderable, Text):
        return renderable, renderable.plain
    if isinstance(renderable, Content):
        return renderable, renderable.plain
    if not is_renderable(renderable):
        s = str(renderable)
        return s, s

    w = max(40, int(width) or 100)
    # Capture to a buffer (not the real TTY). Large height avoids soft-clipping
    # long Syntax/Markdown bodies to the terminal row count.
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=w,
        height=max(10_000, w),
        soft_wrap=True,
        legacy_windows=False,
    )
    try:
        console.print(renderable)
    except Exception:
        s = str(renderable)
        return s, s
    plain = buf.getvalue().rstrip("\n")
    if not plain:
        s = str(renderable)
        return s, s
    # Text without ANSI — selection extract uses plain coordinates.
    return Text(plain), plain


def plain_from_renderable(renderable: object, *, width: int = 100) -> str:
    """Best-effort plain text (clipboard / tests).

    :param renderable: String, Rich text, or other console-renderable.
    :param width: Console width for wrapping.
    :returns: Plain text without ANSI.
    """
    _visual, plain = materialize_selectable(renderable, width=width)
    return plain


class SelectableStatic(Static):
    """:class:`~textual.widgets.Static` with reliable text selection and yank.

    Displays the original rich renderable (so the detail pane reflows and is
    not pre-truncated). Maintains a plain-text cache for :meth:`get_selection`
    fallback and full-pane yank.
    """

    ALLOW_SELECT = True

    def __init__(self, content: VisualType = "", **kwargs) -> None:  # Textual Static
        super().__init__(content, **kwargs)
        self._source: object = content
        self._plain_cache: str = plain_from_renderable(content, width=100)
        self._materialize_width: int = 100

    def _widget_width(self) -> int:
        try:
            w = int(self.size.width) or 0
            if w < 20:
                parent = self.parent
                # parent may be a plain DOMNode without size (mypy).
                pw = getattr(parent, "size", None) if parent is not None else None
                if pw is not None:
                    w = int(pw.width) or 0
            return max(40, w or 100)
        except Exception:
            return 100

    def _refresh_plain_cache(self) -> None:
        width = self._widget_width()
        self._materialize_width = width
        self._plain_cache = plain_from_renderable(self._source, width=width)

    def update(self, content: VisualType = "", *, layout: bool = True) -> None:
        """Update display with the original renderable; refresh plain cache."""
        self._source = content
        # Show Markdown/Syntax/Group as-is — reflows with the pane; no fixed-width bake.
        super().update(content, layout=layout)
        self._refresh_plain_cache()

    def on_resize(self, event: events.Resize) -> None:
        """Recompute plain cache when width changes (selection line offsets)."""
        width = self._widget_width()
        if width == self._materialize_width:
            return
        if self._source is None or self._source == "":
            self._materialize_width = width
            return
        self._refresh_plain_cache()

    def get_plain_text(self) -> str:
        """Return the full plain-text cache for the current content."""
        return self._plain_cache

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Extract the dragged region; fall back to plain cache for rich bodies.

        Prefer Textual's native extract when the visual is ``Text``/``Content``.
        For Markdown/Syntax/Group, extract from the plain cache so a drag still
        yields a region (coordinates track the plain wrap width).
        """
        try:
            result = super().get_selection(selection)
        except Exception:
            result = None
        if result is not None:
            text, end = result
            if text:
                return text, end
        plain = self._plain_cache or ""
        if not plain:
            return None
        try:
            extracted = selection.extract(plain)
        except Exception:
            return None
        if not extracted:
            return None
        return extracted, "\n"
