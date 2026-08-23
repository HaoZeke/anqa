"""Selectable body text for the TUI (drag-select + plain clipboard yank).

Bodies display as Textual :class:`~textual.content.Content` so drag
highlight and extract work. Syntax uses Pygments via ``Syntax.highlight``
(same theme as :func:`~groket.ui.render_detail._syntax`, no ANSI round-trip).
Markdown keeps emphasis via a console bake. Rules become short section
lines. Full tool bodies past display mid-caps are rebuilt in DetailView.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.protocol import is_renderable
from rich.rule import Rule
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.content import Content
from textual.selection import Selection
from textual.visual import VisualType
from textual.widgets import Static

# Markdown bake width. Rich headings include a Rule of this width — do
# not use a huge value or Notes cards explode into a wall of dashes.
_MD_WIDTH = 88
_CHROME_WIDTH = 100


def _rstrip_lines(plain: str) -> str:
    """Drop trailing spaces Rich adds when padding to console width."""
    if not plain:
        return ""
    return "\n".join(line.rstrip() for line in plain.splitlines()).rstrip("\n")


def _coerce_style(style: Style | str | None) -> Style | None:
    """Normalize Rich span styles (objects or markup strings like ``bold``)."""
    if style is None:
        return None
    if isinstance(style, str):
        try:
            return Style.parse(style) if style.strip() else None
        except Exception:
            return None
    return style


def _style_without_default_bg(style: Style | str | None) -> Style | None:
    """Drop default/null backgrounds that paint black cells in Textual Content."""
    st = _coerce_style(style)
    if st is None:
        return None
    bg = st.bgcolor
    drop_bg = True
    if bg is not None:
        num = getattr(bg, "number", None)
        name = str(getattr(bg, "name", "") or "")
        # Keep real palette / truecolor; drop "default" and unset.
        if name and name not in ("default", "none"):
            drop_bg = False
        elif num is not None and num >= 0:
            drop_bg = False
        elif getattr(bg, "triplet", None) is not None and num != -1:
            drop_bg = False
    return Style(
        color=st.color,
        bgcolor=None if drop_bg else bg,
        bold=True if st.bold else None,
        dim=True if st.dim else None,
        italic=True if st.italic else None,
        underline=True if st.underline else None,
        strike=True if st.strike else None,
        reverse=True if st.reverse else None,
    )


def _style_is_visible(st: Style | None) -> bool:
    """True when *st* paints something (color or face)."""
    if st is None:
        return False
    return bool(st.color is not None or st.bold or st.italic or st.dim or st.underline or st.strike)


def _content_from_text(text: Text) -> Content:
    """Build Content from Rich Text; keep base style and spans.

    ``Text(body, style=\"yellow\")`` stores yellow on ``Text.style``, not in
    spans — system/session chrome bodies use that form. Dropping the base
    style made system detail render as unstyled white.
    """
    if not text.plain:
        return Content("")
    cleaned = Text(text.plain)
    base = _style_without_default_bg(getattr(text, "style", None))
    if base is not None and _style_is_visible(base):
        cleaned.stylize(base, 0, len(cleaned.plain))
    for span in text.spans:
        start, end, style = span.start, span.end, span.style
        st = _style_without_default_bg(style)
        if st is not None and _style_is_visible(st):
            cleaned.stylize(st, start, end)
    return Content.from_rich_text(cleaned)


def _console_text(renderable: object, *, width: int) -> Text:
    """Render *renderable* to styled Text; strip per-line width padding.

    Writes only into an in-memory buffer (never the process stdout).
    """
    w = max(20, int(width) or 100)
    sink = StringIO()
    console = Console(
        file=sink,
        force_terminal=True,
        color_system="truecolor",
        width=w,
        record=True,
        legacy_windows=False,
    )
    try:
        console.print(renderable)
        ansi = console.export_text(styles=True)
    except Exception:
        return Text(str(renderable))
    if not ansi:
        return Text("")
    styled = Text.from_ansi(ansi)
    out = Text()
    for i, line in enumerate(styled.split("\n")):
        plain = line.plain.rstrip()
        clipped = line[: len(plain)]
        if i:
            out.append("\n")
        out.append_text(clipped)
    return out


def _console_plain(renderable: object, *, width: int) -> str:
    """Plain text via console at *width* (rstrip pad)."""
    w = max(20, min(int(width) or 100, 240))
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
        return str(renderable)
    return _rstrip_lines(buf.getvalue())


def _rule_content(rule: Rule) -> Content:
    """Short rule line for Content display (never bake Rule at prose width).

    Trailing newline matches Rich ``Rule`` (own line before the next body).
    """
    title = str(getattr(rule, "title", None) or "").strip()
    if title:
        plain_title = Text.from_markup(title).plain if "[" in title else title
        return Content(f"── {plain_title} ──\n")
    return Content("─" * 40 + "\n")


def _styled_content(renderable: object, *, width: int) -> Content:
    """Console-render *renderable* to Content, keeping foreground styles."""
    text = _console_text(renderable, width=width)
    return _content_from_text(text)


def _syntax_content(syntax: Syntax) -> Content:
    """Pygments highlight via the same Syntax object product code built.

    Uses :meth:`Syntax.highlight` (not ANSI export) so theme colors match
    the detail pane before selectable bake, without black cell backgrounds.
    """
    code = syntax.code or ""
    if not code.strip():
        return Content("")
    try:
        highlighted = syntax.highlight(code)
    except Exception:
        return Content(code)
    content = _content_from_text(highlighted)
    if not content.plain or len(content.plain) < len(code):
        return Content(code)
    return content


def to_display_content(renderable: object) -> Content | str:
    """Body renderable → styled Content so drag-select has offset meta.

    Syntax keeps Pygments colors from the product :class:`~rich.syntax.Syntax`
    instance. Markdown keeps emphasis. Rules are short section lines.
    """
    if renderable is None:
        return ""
    if isinstance(renderable, Content):
        return renderable
    if isinstance(renderable, str):
        return renderable
    if isinstance(renderable, Text):
        return _content_from_text(renderable)
    if isinstance(renderable, Padding):
        return to_display_content(renderable.renderable)
    if isinstance(renderable, Rule):
        return _rule_content(renderable)
    if isinstance(renderable, Markdown):
        content = _styled_content(renderable, width=_MD_WIDTH)
        if content.plain.strip():
            return content
        markup = str(getattr(renderable, "markup", None) or "")
        return Content(markup) if markup else Content("")
    if isinstance(renderable, Syntax):
        return _syntax_content(renderable)
    if isinstance(renderable, Group):
        # Concatenate like Rich Group (no forced newline between children).
        # System detail is head Text (no trailing \\n after time) + body Text —
        # inserting "\\n" between every child reflowed chrome and dropped the
        # native line packing.
        parts: list[Content] = []
        for child in renderable.renderables or ():
            piece = to_display_content(child)
            if isinstance(piece, str):
                parts.append(Content(piece))
            else:
                parts.append(piece)
        if not parts:
            return Content("")
        out = parts[0]
        for part in parts[1:]:
            out = out + part
        return out
    if isinstance(renderable, Table):
        return _styled_content(renderable, width=_CHROME_WIDTH)
    if not is_renderable(renderable):
        return str(renderable)
    content = _styled_content(renderable, width=_CHROME_WIDTH)
    return content if content.plain else Content(str(renderable))


def display_plain(display: object) -> str:
    """Unwrapped plain of the on-screen visual (selection coordinates)."""
    if display is None:
        return ""
    if isinstance(display, Content):
        return display.plain
    if isinstance(display, str):
        return display
    if isinstance(display, Text):
        return display.plain
    return display_plain(to_display_content(display))


def plain_from_renderable(renderable: object, *, width: int = 100, full: bool = False) -> str:
    """Plain text for clipboard / tests from a body renderable.

    :param renderable: String, Rich text, Markdown, Syntax, Group, …
    :param width: Chrome console width when baking Tables/Rules.
    :param full: Prefer Markdown ``.markup`` / Syntax ``.code`` (paste-ready
        source) when set. When False, return display plain (what selection sees).
    :returns: Plain text without catastrophic Rule/space padding.
    """
    w = max(20, int(width) or _CHROME_WIDTH)
    if renderable is None:
        return ""
    if full:
        if isinstance(renderable, Markdown):
            markup = str(getattr(renderable, "markup", None) or "")
            if markup:
                return markup
        if isinstance(renderable, Syntax):
            return renderable.code or ""
        if isinstance(renderable, Padding):
            return plain_from_renderable(renderable.renderable, width=w, full=True)
        if isinstance(renderable, Group):
            parts = [
                plain_from_renderable(child, width=w, full=True)
                for child in (renderable.renderables or ())
            ]
            return "\n".join(p for p in parts if p)
        if isinstance(renderable, Text):
            return _rstrip_lines(renderable.plain)
        if isinstance(renderable, Content):
            return _rstrip_lines(renderable.plain)
        if isinstance(renderable, str):
            return renderable
        if isinstance(renderable, Rule):
            title = str(getattr(renderable, "title", None) or "").strip()
            if title:
                plain_title = Text.from_markup(title).plain if "[" in title else title
                return f"── {plain_title} ──"
            return "─" * 40
        if not is_renderable(renderable):
            return str(renderable)
        return _console_plain(renderable, width=min(w, 240))
    return display_plain(to_display_content(renderable))


def prepare_body(renderable: object) -> tuple[VisualType, str, str]:
    """Display Content, selection plain, and full-body yank plain.

    Selection plain matches on-screen Content (Textual offset space).
    Yank plain prefers Markdown markup / Syntax code for paste-ready quotes.
    """
    if renderable is None or renderable == "":
        return "", "", ""
    display = to_display_content(renderable)
    select_plain = display_plain(display)
    yank_plain = plain_from_renderable(renderable, full=True)
    if not yank_plain:
        yank_plain = select_plain
    return display, select_plain, yank_plain


class SelectableStatic(Static):
    """Extractable body displayed as Textual Content (drag-select + yank).

    Focusable for Tab + ``y``. Mouse click does not steal focus (no raised
    card chrome). Display keeps Syntax/Markdown styling; full-body yank uses
    source markup/code when available so operators paste ``##`` headings.

    **Contract:** quote-worthy TUI bodies use this widget (not plain
    :class:`~textual.widgets.Static`). See AGENTS.md §6.5a.
    """

    ALLOW_SELECT = True
    can_focus = True

    def __init__(self, content: VisualType = "", **kwargs) -> None:  # Textual Static
        display, select_plain, yank_plain = prepare_body(
            content if content not in ("", None) else ""
        )
        super().__init__(display, **kwargs)
        self._select_plain: str = select_plain
        self._yank_plain: str = yank_plain

    def focus_on_click(self) -> bool:
        """Drag-select without stealing focus or raising the pane chrome."""
        return False

    def update(self, content: VisualType = "", *, layout: bool = True) -> None:
        """Show body; refresh selection and full-yank plains."""
        display, select_plain, yank_plain = prepare_body(
            content if content not in ("", None) else ""
        )
        super().update(display, layout=layout)
        self._select_plain = select_plain
        self._yank_plain = yank_plain

    def get_plain_text(self) -> str:
        """Full-body yank plain (Markdown source / Syntax code when present)."""
        return self._yank_plain

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Extract drag region using on-screen (display) plain coordinates."""
        plain = self._select_plain
        if not plain:
            return None
        try:
            extracted = selection.extract(plain)
        except Exception:
            return None
        if not extracted:
            return None
        return extracted, "\n"


def is_extractable_static(widget: object) -> bool:
    """True when *widget* is a :class:`SelectableStatic` body (not chrome)."""
    return isinstance(widget, SelectableStatic)
