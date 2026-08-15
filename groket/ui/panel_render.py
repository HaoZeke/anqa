"""Consistent panel rendering: UI chrome vs Markdown payloads.

UI structure (titles, keys, sections, status, lists) uses Rich Text/Rule
only — never markdown strings. No decorative glyphs; section = bold title +
dim rule.

Empty panes use :class:`EmptyState`. Keys live in the Footer, ``?``, and
the command palette.

Markdown payloads (assistant text, plugin reports, MD diffs) use md_content().
"""

from __future__ import annotations

import re
from contextlib import suppress

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.rule import Rule
from rich.text import Text
from textual.widgets import Static

from .keys import format_key_chord

EMPTY_STATE_CLASS = "empty-state"


def looks_like_markdown(text: str) -> bool:
    s = (text or "").lstrip()
    if not s:
        return False
    if s.startswith("#") or "```" in s:
        return True
    if s.startswith(("- ", "* ", "> ")):
        return True
    if "**" in s or "__" in s or "](http" in s or ("](/" in s):
        return True
    if "\n## " in s or "\n# " in s:
        return True
    return False


def md_content(text: str, *, max_chars: int = 120000, indent: int = 2) -> RenderableType:
    body = (text or "").strip()
    if not body:
        return Text("(empty)\n", style="dim")
    if len(body) > max_chars:
        half = max_chars // 2
        body = body[:half] + "\n\n…\n\n" + body[-half:]
    rendered: RenderableType
    try:
        rendered = Markdown(body)
    except Exception:
        rendered = Text(body)
    if indent:
        return Padding(rendered, (0, 0, 0, indent))
    return rendered


def content_block(text: str, *, max_chars: int = 120000, indent: int = 2) -> RenderableType:
    if looks_like_markdown(text):
        return md_content(text, max_chars=max_chars, indent=indent)
    body = text or ""
    if len(body) > max_chars:
        body = body[: max_chars // 2] + "\n…\n" + body[-(max_chars // 2) :]
    t = Text(body)
    if not body.endswith("\n"):
        t.append("\n")
    return Padding(t, (0, 0, 0, indent)) if indent else t


def section_header(title: str) -> Text:
    """Bold section title + dim horizontal rule (standard TUI pattern, no icon)."""
    t = Text()
    t.append("\n")
    t.append(f"{title}\n", style="bold")
    t.append("─" * min(40, max(12, len(title) + 4)) + "\n", style="dim")
    return t


def kv_line(key: str, value: str, *, key_width: int = 12) -> Text:
    t = Text()
    t.append(f"  {key:<{key_width}} ", style="dim")
    t.append(f"{value}\n")
    return t


def list_row(label: str, *, meta: str = "") -> Text:
    """Indented list row — no bullet glyph."""
    t = Text()
    t.append("  ")
    t.append(label)
    if meta:
        t.append(meta, style="dim")
    t.append("\n")
    return t


def bullet(label: str, *, detail: str = "") -> Text:
    """Alias for list_row (historical name)."""
    return list_row(label, meta=detail)


def status_chip(label: str, *, kind: str = "unknown") -> Text:
    """Inline status using the shared run/outcome palette."""
    from .styles import status_rich_style

    kind_l = (kind or "unknown").lower()
    if kind_l in ("ok", "success", "completed"):
        style = status_rich_style("completed")
    elif kind_l in ("bad", "error", "failed"):
        style = status_rich_style("failed")
    elif kind_l in ("running", "active"):
        style = status_rich_style("running")
    elif kind_l in ("ending", "finishing"):
        style = status_rich_style("ending")
    elif kind_l in ("pending", "idle", "unknown"):
        style = status_rich_style("idle")
    else:
        style = status_rich_style(kind_l)
    chip = Text()
    chip.append(label, style=style)
    return chip


def _footer_key_rich_style() -> str:
    """Match Textual ``FooterKey`` / ``.footer-key--key`` (bold + accent, not reverse).

    Footer uses ``color: $footer-key-foreground`` (theme accent) and
    ``background: $footer-key-background`` (usually transparent) with ``text-style: bold``
    and horizontal padding — not inverted video.
    """
    try:
        from textual.app import App

        app = getattr(App, "get_running_app", lambda: None)()
        if app is not None:
            vars_ = getattr(app, "get_css_variables", None)
            if callable(vars_):
                css_vars = vars_() or {}
                fg = (
                    css_vars.get("footer-key-foreground")
                    or css_vars.get("accent")
                    or css_vars.get("primary")
                )
                if fg:
                    return f"bold {fg}"
            theme = getattr(app, "theme", None) or ""
            _ = theme
    except Exception:
        pass
    return "bold"


def key_chip(key: str) -> Text:
    """Key label styled like Textual Footer bindings (bold accent, not reverse video).

    Mirrors ``FooterKey`` component: bold + ``$footer-key-foreground``, padding
    ``0 1`` (spaces), transparent background — same look as the app Footer.
    """
    label = (key or "").strip() or "?"
    display = format_key_chord(label)
    style = _footer_key_rich_style()
    t = Text()
    t.append(f" {display} ", style=style)
    return t


def keys_rich(message: str) -> Text:
    """Inline text with `` `key` `` segments as :func:`key_chip`."""
    t = Text()
    _append_tip_body(t, message, body_style="")
    return t


def _append_tip_body(t: Text, message: str, *, body_style: str = "dim") -> None:
    """Append text; segments in `backticks` render as :func:`key_chip`."""
    body = (message or "").strip()
    if not body:
        return
    parts = re.split("(`[^`]+`)", body)
    for part in parts:
        if part.startswith("`") and part.endswith("`") and (len(part) >= 3):
            t.append_text(key_chip(part[1:-1]))
        elif body_style:
            t.append(part, style=body_style)
        else:
            t.append(part)


class EmptyState(Static):
    """Quiet empty-pane chrome: one dim line, no border.

    Use when a section has nothing to show yet (no flags, no notes, no analysis).
    Keys stay in the Footer and ``?``. Always visible when a message is set.
    """

    DEFAULT_CSS = """
    EmptyState {
        height: auto;
        width: 100%;
        max-width: 100%;
        margin: 0 0 1 0;
        padding: 0 1;
        color: $text-muted;
        border: none;
        background: transparent;
    }
    EmptyState.empty-state-hidden {
        display: none;
        height: 0;
        margin: 0;
        padding: 0;
    }
    """

    def __init__(
        self,
        message: str = "",
        *,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        name: str | None = None,
    ) -> None:
        self._empty_message = (message or "").strip()
        extra = EMPTY_STATE_CLASS
        if classes:
            for part in str(classes).split():
                if part and part != EMPTY_STATE_CLASS:
                    extra = f"{extra} {part}"
        super().__init__(
            self._render_body(),
            id=id,
            classes=extra,
            disabled=disabled,
            name=name,
        )
        self._sync_hidden()

    def set_message(self, message: str) -> None:
        """Show *message* as dim empty chrome, or hide when empty."""
        self._empty_message = (message or "").strip()
        self._sync_hidden()
        with suppress(Exception):
            self.update(self._render_body())

    def clear_message(self) -> None:
        """Hide this empty state."""
        self.set_message("")

    def _render_body(self) -> Text:
        if not self._empty_message:
            return Text("")
        t = Text()
        _append_tip_body(t, self._empty_message, body_style="dim")
        return t

    def _sync_hidden(self) -> None:
        hidden = not self._empty_message
        with suppress(Exception):
            if hidden:
                self.add_class("empty-state-hidden")
            else:
                self.remove_class("empty-state-hidden")


def meta_strip(parts: list[str]) -> Text:
    """``a  ·  b  ·  c`` with dim separators; callers pass full words."""
    t = Text("  ")
    for i, part in enumerate(parts):
        if i:
            t.append("  ·  ", style="dim")
        t.append(part)
    t.append("\n")
    return t


def dim_rule() -> Rule:
    return Rule(style="dim")


def panel_group(*parts: RenderableType | None) -> RenderableType:
    blocks = [p for p in parts if p is not None]
    if not blocks:
        return Text("")
    if len(blocks) == 1:
        return blocks[0]
    return Group(*blocks)
