"""Header small mark, help mark, and paths banner."""

from __future__ import annotations

from pathlib import Path

import pytest
from anqa.ui.app import AnqaApp
from anqa.ui.brand_mark import (
    COMPLETE,
    FAILED,
    RUNNING,
    AppChrome,
    AppFooter,
    help_mark,
    paths_banner,
)
from anqa.ui.widgets.activity_bar import ActivityBar
from anqa.ui.widgets.help_modal import HelpModal
from textual.app import App, ComposeResult
from textual.widgets import Static

from .pilot_helpers import static_plain, wait_until


def _styles(mark) -> str:
    return "".join(str(span.style) for span in mark.spans)


def test_paths_banner_is_host_line() -> None:
    line = paths_banner(Path("/tmp/sessions"))
    assert "Host" in line
    assert "█" not in line
    assert "Eval" not in line


def test_help_mark_is_three_slats() -> None:
    mark = help_mark()
    assert mark.plain.splitlines() == ["██████", "██████", "██████"]
    styles = _styles(mark)
    assert COMPLETE in styles
    assert FAILED in styles
    assert RUNNING in styles


def _make_app(tmp_path: Path) -> AnqaApp:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    return AnqaApp(traces_path=traces)


@pytest.mark.asyncio
async def test_home_chrome_is_one_row(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        chrome = app.query_one(AppChrome)
        assert chrome.size.height == 1
        assert "anqa" in static_plain(chrome.query_one("#app-chrome-title", Static)).lower()
        assert chrome.query_one(ActivityBar)
        footer = app.query_one(AppFooter)
        assert footer.has_class("-compact")
        banner = chrome.query_one("#session-paths", Static)
        assert "Host" in static_plain(banner)
        kids = [w.id for w in chrome.children]
        assert kids[0] == "session-paths"
        assert "app-chrome-title" in kids
        assert kids[-1] == "activity-bar"
        assert app.sub_title == ""


@pytest.mark.asyncio
async def test_home_chrome_hides_paths_when_narrow(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        banner = app.query_one("#session-paths", Static)
        assert static_plain(banner) == ""


@pytest.mark.asyncio
async def test_help_modal_has_no_logo() -> None:
    class H(App[None]):
        def compose(self) -> ComposeResult:
            yield Static("main")

    app = H()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(HelpModal())
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#help-modal-text"))),
            description="help text mounted",
        )
        assert not list(app.screen.query("#help-brand-mark"))
        assert not list(app.screen.query("#help-brand-name"))
