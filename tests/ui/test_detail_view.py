"""DetailView widget tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from anqa.ui.widgets.detail_view import DetailView
from conftest import make_trace_event
from textual.app import App, ComposeResult

# 1×1 PNG so the widget has a real file without Pillow in the test.
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _DetailApp(App):
    def compose(self) -> ComposeResult:
        yield DetailView(id="detail")


@pytest.mark.asyncio
async def test_detail_view_show_event() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="read_file",
            raw_input={"target_file": "x.py"},
        )
        dv.show_event(ev)
        assert "read file" in dv.visible_plain()
        assert dv.query_one("#detail-sec-input").display
        assert dv._current_event is ev


@pytest.mark.asyncio
async def test_detail_same_event_skips_scroll_home() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="read_file",
            raw_input={"target_file": "x.py"},
        )
        homes: list[int] = []
        orig = dv.scroll_home

        def _home(*_a: object, **_k: object) -> None:
            homes.append(1)
            orig(animate=False)

        dv.scroll_home = _home  # type: ignore[method-assign]
        dv.show_event(ev)
        assert homes == [1]
        dv.show_event(ev)
        assert homes == [1]


@pytest.mark.asyncio
async def test_detail_view_show_event_with_pairs() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        call = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="read_file",
            raw_input={"target_file": "x.py"},
            tool_call_id="c1",
        )
        result = make_trace_event(
            index=1,
            event_type="tool_call_update",
            tool_name="read_file",
            content="file content",
            tool_call_id="c1",
        )
        dv.show_event(call, paired_call=call, paired_result=result)
        assert "read file" in dv.visible_plain()
        assert dv._paired_call is call
        assert dv._paired_result is result


@pytest.mark.asyncio
async def test_detail_view_clear() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(index=0, event_type="user_message_chunk", content="hello")
        dv.show_event(ev)
        assert dv._current_event is not None
        dv.clear_detail()
        assert dv._current_event is None


@pytest.mark.asyncio
async def test_detail_view_no_event() -> None:
    app = _DetailApp()
    async with app.run_test() as pilot:
        from .pilot_helpers import wait_until

        await wait_until(
            pilot,
            lambda: bool(list(app.query("#detail-body"))),
            description="detail-body mounted",
        )
        dv = app.query_one("#detail", DetailView)
        dv._current_event = None
        dv._refresh_content()
        assert dv.visible_plain().strip() == ""


def _image_event(path: Path) -> object:
    return make_trace_event(
        index=1,
        event_type="tool_call_update",
        tool_name="image_gen",
        content=json.dumps({"path": str(path), "message": "saved"}),
    )


@pytest.mark.asyncio
async def test_detail_view_shows_image_file_for_image_gen(tmp_path: Path) -> None:
    png = tmp_path / "out.png"
    png.write_bytes(_PNG)
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        dv.show_event(_image_event(png))
        img = dv.query_one("#detail-image")
        assert img.display is True
        assert str(png) in dv.visible_plain()
        yanked = dv.get_plain_text()
        assert str(png) in yanked
        assert "saved" in yanked
        assert "sixel" not in yanked.lower()


@pytest.mark.asyncio
async def test_detail_view_hides_image_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "gone.jpg"
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        dv.show_event(_image_event(missing))
        img = dv.query_one("#detail-image")
        assert img.display is False
        assert str(missing) in dv.visible_plain()


@pytest.mark.asyncio
async def test_detail_view_hides_image_for_non_image_tool() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        dv.show_event(
            make_trace_event(
                index=0,
                event_type="tool_call",
                tool_name="read_file",
                raw_input={"target_file": "x.py"},
            )
        )
        img = dv.query_one("#detail-image")
        assert img.display is False
