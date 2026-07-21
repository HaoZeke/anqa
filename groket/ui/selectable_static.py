"""Static content that supports Textual text selection and plain-text copy.

Detail panes often hold Markdown / Syntax / Group renderables. Textual's
default :meth:`Widget.get_selection` only extracts from ``Text`` / ``Content``,
so mouse selection looks broken and Ctrl+C cannot copy. This widget keeps a
plain-text cache and falls back to it when rich extraction fails.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.protocol import is_renderable
from rich.text import Text
from textual.selection import Selection
from textual.visual import VisualType
from textual.widgets import Static


def plain_from_renderable(renderable: object, *, width: int = 100) -> str:
    """Best-effort plain text for clipboard / selection fallback.

    :param renderable: String, Rich text, or other console-renderable.
    :param width: Console width for wrapping (affects multi-line extract).
    :returns: Plain text without ANSI.
    """
    if renderable is None:
        return ""
    if isinstance(renderable, str):
        return renderable
    if isinstance(renderable, Text):
        return renderable.plain
    # Textual Content (if present)
    plain_attr = getattr(renderable, "plain", None)
    if isinstance(plain_attr, str):
        return plain_attr
    if not is_renderable(renderable):
        return str(renderable)
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=max(40, int(width) or 100),
        legacy_windows=False,
        soft_wrap=True,
    )
    try:
        console.print(renderable)
    except Exception:
        return str(renderable)
    return buf.getvalue()


class SelectableStatic(Static):
    """:class:`~textual.widgets.Static` with reliable text selection and yank."""

    ALLOW_SELECT = True

    def __init__(self, content: VisualType = "", **kwargs) -> None:  # Textual Static
        super().__init__(content, **kwargs)
        self._plain_cache: str = plain_from_renderable(content)

    def update(self, content: VisualType = "", *, layout: bool = True) -> None:
        """Update display and refresh the plain-text cache."""
        super().update(content, layout=layout)
        width = 100
        try:
            width = max(40, self.size.width or 100)
        except Exception:
            width = 100
        self._plain_cache = plain_from_renderable(content, width=width)

    def get_plain_text(self) -> str:
        """Return the full plain-text cache for the current content."""
        return self._plain_cache

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Extract selection; fall back to plain cache for rich content.

        When the body is Markdown/Syntax/Group, Textual cannot slice the visual
        directly. Prefer :meth:`Selection.extract` on the plain cache so a drag
        still yields a region; if that fails, return the full body.
        """
        try:
            result = super().get_selection(selection)
        except Exception:
            result = None
        if result is not None:
            return result
        plain = (self._plain_cache or "").rstrip("\n")
        if not plain:
            return None
        try:
            extracted = selection.extract(plain)
        except Exception:
            extracted = plain
        if not extracted:
            # Offsets can miss when visual layout ≠ plain layout; still allow yank.
            extracted = plain
        return extracted, "\n"
