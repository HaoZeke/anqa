"""Static content that supports Textual text selection and plain-text copy.

Detail panes often hold Markdown / Syntax / Group renderables. Textual's
default :meth:`Widget.get_selection` only extracts from ``Text`` / ``Content``,
so mouse drag selection cannot return a line or region. This widget
**materializes** rich content into styled :class:`~rich.text.Text` at the
current width so on-screen coordinates match the clipboard extract.
"""

from __future__ import annotations

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

    :param renderable: String, Rich text, Markdown/Syntax/Group, or similar.
    :param width: Wrap width (must match the widget for correct line offsets).
    :returns: ``(visual_for_static, plain_text)``.
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
    # record=True + export_text(styles=True) preserves colour; styles=False is
    # empty on some Rich builds, so plain always comes from Text.from_ansi.
    console = Console(
        record=True,
        force_terminal=True,
        color_system="truecolor",
        width=w,
        soft_wrap=True,
        legacy_windows=False,
    )
    try:
        console.print(renderable)
        ansi = console.export_text(styles=True)
    except Exception:
        s = str(renderable)
        return s, s
    if not ansi:
        s = str(renderable)
        return s, s
    text = Text.from_ansi(ansi)
    # Trailing spaces from Syntax padding stay; drop a final newline only so
    # Selection line indices match the last visible line.
    plain = text.plain.rstrip("\n")
    return text, plain


def plain_from_renderable(renderable: object, *, width: int = 100) -> str:
    """Best-effort plain text (clipboard / tests).

    :param renderable: String, Rich text, or other console-renderable.
    :param width: Console width for wrapping.
    :returns: Plain text without ANSI.
    """
    _visual, plain = materialize_selectable(renderable, width=width)
    return plain


class SelectableStatic(Static):
    """:class:`~textual.widgets.Static` with reliable partial text selection."""

    ALLOW_SELECT = True

    def __init__(self, content: VisualType = "", **kwargs) -> None:  # Textual Static
        width = 100
        visual, plain = materialize_selectable(content, width=width)
        super().__init__(visual, **kwargs)
        self._source: object = content
        self._plain_cache: str = plain
        self._materialize_width: int = width

    def _widget_width(self) -> int:
        try:
            return max(40, int(self.size.width) or 100)
        except Exception:
            return 100

    def update(self, content: VisualType = "", *, layout: bool = True) -> None:
        """Materialize *content* to selectable Text and refresh the plain cache."""
        self._source = content
        width = self._widget_width()
        visual, plain = materialize_selectable(content, width=width)
        self._materialize_width = width
        self._plain_cache = plain
        super().update(visual, layout=layout)

    def on_resize(self, event: events.Resize) -> None:
        """Re-wrap when the pane width changes so selection line offsets stay valid."""
        width = self._widget_width()
        if width == self._materialize_width:
            return
        if self._source is None or self._source == "":
            self._materialize_width = width
            return
        visual, plain = materialize_selectable(self._source, width=width)
        self._materialize_width = width
        self._plain_cache = plain
        super().update(visual, layout=True)

    def get_plain_text(self) -> str:
        """Return the full plain-text cache for the current content."""
        return self._plain_cache

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Extract the dragged region (line / partial line / multi-line).

        Content is materialized to :class:`~rich.text.Text`, so Textual's native
        extract usually succeeds. Fall back to :meth:`Selection.extract` on the
        plain cache (same line layout as the visual).
        """
        try:
            result = super().get_selection(selection)
        except Exception:
            result = None
        if result is not None:
            text, end = result
            # Empty extract = zero-width drag; treat as no selection.
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
