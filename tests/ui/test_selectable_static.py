"""SelectableStatic plain-text cache and partial (line) selection."""

from __future__ import annotations

import pytest
from conftest import make_trace_event
from groket.ui.selectable_static import (
    SelectableStatic,
    materialize_selectable,
    plain_from_renderable,
)
from groket.ui.widgets.detail_view import DetailView
from rich.console import Group
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


def test_materialize_group_preserves_line_for_partial_extract() -> None:
    """Rich Group → Text so Selection.extract can take one line / a word."""
    body = Group(
        Text("#0 user message"),
        Text(""),
        Text("first line of the prompt"),
        Text("second line ONLY_THIS_LINE"),
        Text("third line"),
    )
    _vis, plain = materialize_selectable(body, width=60)
    lines = plain.splitlines()
    idx = next(i for i, ln in enumerate(lines) if "ONLY_THIS_LINE" in ln)
    full_line = Selection(Offset(0, idx), Offset(len(lines[idx]), idx))
    assert "ONLY_THIS_LINE" in full_line.extract(plain)
    assert "first line" not in full_line.extract(plain)
    start = lines[idx].index("ONLY")
    word = Selection(Offset(start, idx), Offset(start + 4, idx))
    assert word.extract(plain) == "ONLY"


class _SelApp(App):
    def compose(self) -> ComposeResult:
        yield SelectableStatic("line one\nline two\nline three", id="body")


@pytest.mark.asyncio
async def test_selectable_static_plain_cache_and_partial_selection() -> None:
    app = _SelApp()
    async with app.run_test():
        body = app.query_one("#body", SelectableStatic)
        assert "line one" in body.get_plain_text()
        # First line, columns 0–4 → "line"
        sel = Selection(Offset(0, 0), Offset(4, 0))
        got = body.get_selection(sel)
        assert got is not None
        text, _end = got
        assert text == "line"
        # Whole second line
        sel2 = Selection(Offset(0, 1), Offset(8, 1))
        got2 = body.get_selection(sel2)
        assert got2 is not None
        assert got2[0] == "line two"


class _MdApp(App):
    def compose(self) -> ComposeResult:
        yield SelectableStatic("", id="body")


@pytest.mark.asyncio
async def test_selectable_static_markdown_partial_line_selection() -> None:
    """Markdown is materialized to Text; a line-scoped selection is partial."""
    app = _MdApp()
    async with app.run_test():
        body = app.query_one("#body", SelectableStatic)
        body.update(
            Group(
                Text("meta header"),
                Markdown("## Section\n\nAgent said hello world uniquely"),
            )
        )
        plain = body.get_plain_text()
        assert "hello world uniquely" in plain
        lines = plain.splitlines()
        idx = next(i for i, ln in enumerate(lines) if "hello world" in ln)
        sel = Selection(Offset(0, idx), Offset(len(lines[idx]), idx))
        got = body.get_selection(sel)
        assert got is not None
        text, end = got
        assert "hello world uniquely" in text
        assert "meta header" not in text
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
async def test_detail_view_multiline_message_partial_line() -> None:
    """Multi-line user prompts keep lines distinct for drag-select."""
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="user_message_chunk",
            content="line alpha\nline bravo UNIQUE99\nline charlie",
        )
        dv.show_event(ev)
        body = dv.query_one("#detail-body", SelectableStatic)
        plain = body.get_plain_text()
        assert "UNIQUE99" in plain
        lines = plain.splitlines()
        idx = next(i for i, ln in enumerate(lines) if "UNIQUE99" in ln)
        sel = Selection(Offset(0, idx), Offset(len(lines[idx]), idx))
        got = body.get_selection(sel)
        assert got is not None
        assert "UNIQUE99" in got[0]
        assert "charlie" not in got[0]


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
