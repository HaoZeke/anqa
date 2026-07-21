"""SelectableStatic plain-text cache and selection fallback."""

from __future__ import annotations

import pytest
from conftest import make_trace_event
from groket.ui.selectable_static import SelectableStatic, plain_from_renderable
from groket.ui.widgets.detail_view import DetailView
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.geometry import Offset
from textual.selection import Selection


def test_plain_from_renderable_str() -> None:
    assert plain_from_renderable("hello") == "hello"
    assert plain_from_renderable(None) == ""


def test_plain_from_renderable_text() -> None:
    assert plain_from_renderable(Text("styled", style="bold")) == "styled"


def test_plain_from_renderable_markdown() -> None:
    plain = plain_from_renderable(Markdown("**bold** and `code`"))
    assert "bold" in plain
    assert "code" in plain


def test_plain_from_renderable_syntax() -> None:
    plain = plain_from_renderable(Syntax("print(1)", "python"))
    assert "print" in plain


class _SelApp(App):
    def compose(self) -> ComposeResult:
        yield SelectableStatic("line one\nline two\nline three", id="body")


@pytest.mark.asyncio
async def test_selectable_static_plain_cache_and_partial_selection() -> None:
    app = _SelApp()
    async with app.run_test():
        body = app.query_one("#body", SelectableStatic)
        assert "line one" in body.get_plain_text()
        # super() path works for plain Text content
        sel = Selection(Offset(0, 0), Offset(4, 0))
        got = body.get_selection(sel)
        assert got is not None
        text, _end = got
        assert "line" in text or text  # non-empty extract


class _MdApp(App):
    def compose(self) -> ComposeResult:
        yield SelectableStatic("", id="body")


@pytest.mark.asyncio
async def test_selectable_static_markdown_fallback_selection() -> None:
    app = _MdApp()
    async with app.run_test():
        body = app.query_one("#body", SelectableStatic)
        body.update(Markdown("# Title\n\nAgent said hello world"))
        plain = body.get_plain_text()
        assert "hello world" in plain or "Title" in plain
        # Force fallback path with a broad selection over the plain cache
        sel = Selection(None, None)
        got = body.get_selection(sel)
        assert got is not None
        text, end = got
        assert text.strip()
        assert end == "\n"


class _DetailApp(App):
    def compose(self) -> ComposeResult:
        yield DetailView(id="detail")


@pytest.mark.asyncio
async def test_detail_view_get_plain_text_yanks_message() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="user_message_chunk",
            content="please copy this exact phrase XYZ123",
        )
        dv.show_event(ev)
        plain = dv.get_plain_text()
        assert "XYZ123" in plain
        assert "please copy" in plain


@pytest.mark.asyncio
async def test_detail_view_get_plain_text_empty_when_cleared() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        assert dv.get_plain_text().strip() == ""
        dv.clear_detail()
        assert dv.get_plain_text().strip() == ""


@pytest.mark.asyncio
async def test_action_copy_detail_yanks_full_body() -> None:
    """Browser y (copy_detail) puts detail plain text on the clipboard."""
    from types import SimpleNamespace

    from groket.ui.screens.browser import BrowserScreen

    class _BrowserCopyApp(App):
        def compose(self) -> ComposeResult:
            yield DetailView(id="detail-panel")

    app = _BrowserCopyApp()
    async with app.run_test():
        dv = app.query_one("#detail-panel", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="agent_message_chunk",
            content="clipboard-target-phrase-99",
        )
        dv.show_event(ev)

        copied: list[str] = []
        notes: list[str] = []
        host = SimpleNamespace(
            get_selected_text=lambda: None,
            query_one=app.query_one,
            app=SimpleNamespace(copy_to_clipboard=lambda text: copied.append(text)),
            notify=lambda msg, **kwargs: notes.append(str(msg)),
        )
        BrowserScreen.action_copy_detail(host)  # type: ignore[arg-type]
        assert copied
        assert "clipboard-target-phrase-99" in copied[0]
        assert notes


@pytest.mark.asyncio
async def test_action_help_quit_copies_selection() -> None:
    """Ctrl+C copies selection when present instead of only showing quit hint."""
    from types import SimpleNamespace

    from groket.ui.app import TraceEvalApp

    copied: list[str] = []
    notes: list[str] = []
    host = SimpleNamespace(
        screen=SimpleNamespace(get_selected_text=lambda: "selected-bit"),
        copy_to_clipboard=lambda text: copied.append(text),
        notify=lambda msg, **kwargs: notes.append(str(msg)),
        active_bindings={},
    )
    TraceEvalApp.action_help_quit(host)  # type: ignore[arg-type]
    assert copied == ["selected-bit"]
    assert notes
