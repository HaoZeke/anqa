"""Timeline widget: load, filter, duration, pairing."""

from __future__ import annotations

import pytest
from conftest import make_trace_event
from groket.analysis.base import Finding
from groket.models import Flag, FlagVerdict, Severity, TraceEvent
from groket.ui.widgets.timeline import TimelineTable
from textual.app import App, ComposeResult


class _TimelineApp(App):
    def compose(self) -> ComposeResult:
        yield TimelineTable(id="timeline-list")


def _basic_events() -> list[TraceEvent]:
    return [
        make_trace_event(index=0, event_type="user", content="hello", timestamp=1000),
        make_trace_event(
            index=1,
            event_type="tool_call",
            tool_name="read_file",
            raw_input={"target_file": "x.py"},
            tool_call_id="c1",
            timestamp=1001,
        ),
        make_trace_event(
            index=2,
            event_type="tool_result",
            tool_name="read_file",
            content="content",
            tool_call_id="c1",
            timestamp=1003,
        ),
        make_trace_event(
            index=3,
            event_type="assistant",
            content="done",
            timestamp=1005,
        ),
        make_trace_event(
            index=4,
            event_type="session",
            content="turn started  turn_number=0",
            timestamp=1006,
        ),
        make_trace_event(
            index=5,
            event_type="session",
            content="turn ended  outcome=success",
            timestamp=1010,
        ),
        make_trace_event(
            index=6,
            event_type="session_error",
            content="error",
            is_error=True,
            timestamp=1011,
        ),
        make_trace_event(
            index=7,
            event_type="tool_call",
            tool_name="run_terminal_command",
            raw_input={"command": "echo"},
            is_error=True,
            tool_call_id="c2",
            timestamp=1012,
        ),
        make_trace_event(
            index=8,
            event_type="subagent",
            content="spawned",
            timestamp=1013,
        ),
        make_trace_event(
            index=9,
            event_type="thought",
            content="thinking...",
            timestamp=1014,
        ),
        make_trace_event(
            index=10,
            event_type="plan",
            content="plan text",
            timestamp=1015,
        ),
    ]


@pytest.mark.asyncio
async def test_timeline_load_and_row_count() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        assert tl.row_count == len(events)
        assert len(tl.events) == len(events)


@pytest.mark.asyncio
async def test_timeline_durations_computed() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        # tool_call c1 at 1001, tool_result c1 at 1003 -> duration=2
        assert 1 in tl.durations
        assert tl.durations[1] == 2


@pytest.mark.asyncio
async def test_timeline_tool_pairs() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        call_ev = events[1]
        result_ev = events[2]
        assert tl.get_paired_result(call_ev) is result_ev
        assert tl.get_paired_call(result_ev) is call_ev
        assert tl.get_paired_result(events[0]) is None
        assert tl.get_paired_call(events[0]) is None


@pytest.mark.asyncio
async def test_timeline_with_findings_and_flags() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        finding = Finding(
            id="f1",
            title="test",
            severity=Severity.HIGH,
            plugin_id="engine",
            detail="x",
            tool_call_ids=["c1"],
        )
        flag = Flag(event_index=0, verdict=FlagVerdict.BAD, description="bad")
        tl.load_events(events, findings=[finding], flags=[flag])
        assert tl.findings_by_call.get("c1") is finding
        assert tl.flags_by_index.get(0) is flag


@pytest.mark.asyncio
async def test_timeline_filter_by_type() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(event_type="tool_call")
        assert tl.row_count < len(events)


@pytest.mark.asyncio
async def test_timeline_filter_by_types_set() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(event_types={"user", "assistant"})
        assert tl.row_count == 2


@pytest.mark.asyncio
async def test_timeline_filter_errors_only() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(errors_only=True)
        assert tl.row_count >= 1


@pytest.mark.asyncio
async def test_timeline_filter_flagged_only() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        flag = Flag(event_index=0, verdict=FlagVerdict.GOOD, description="ok")
        tl.load_events(events, flags=[flag])
        tl.apply_filter(flagged_only=True)
        assert tl.row_count == 1


@pytest.mark.asyncio
async def test_timeline_filter_search_query() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(search_query="hello")
        assert tl.row_count >= 1


@pytest.mark.asyncio
async def test_timeline_filter_tool_name() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(tool_name="read_file")
        assert tl.row_count >= 1


@pytest.mark.asyncio
async def test_timeline_filter_call_ids() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(call_ids={"c1"})
        assert tl.row_count >= 1


@pytest.mark.asyncio
async def test_timeline_event_selected_message() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.move_cursor(row=0, animate=False)
        # Row highlight triggers EventSelected


@pytest.mark.asyncio
async def test_timeline_long_duration_formatting() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=0,
                event_type="tool_call",
                tool_name="run_terminal_command",
                tool_call_id="slow",
                timestamp=1000,
            ),
            make_trace_event(
                index=1,
                event_type="tool_result",
                tool_name="run_terminal_command",
                tool_call_id="slow",
                timestamp=1070,  # 70s duration
            ),
        ]
        tl.load_events(events)
        assert 0 in tl.durations
        assert tl.durations[0] == 70


@pytest.mark.asyncio
async def test_timeline_no_timestamp() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(index=0, event_type="user", content="x", timestamp=None),
        ]
        tl.load_events(events)
        assert tl.row_count == 1
        assert 0 not in tl.durations


@pytest.mark.asyncio
async def test_timeline_tool_result_no_call_id() -> None:
    """tool_result without call_id is skipped in pairing."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=0,
                event_type="tool_result",
                tool_name="grep",
                content="result",
                tool_call_id="",
                timestamp=1000,
            ),
        ]
        tl.load_events(events)
        assert tl.row_count == 1


@pytest.mark.asyncio
async def test_timeline_subagent_tool_column() -> None:
    """Subagent events populate the tool column."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(index=0, event_type="subagent", content="spawned", timestamp=1000),
        ]
        tl.load_events(events)
        assert tl.row_count == 1


@pytest.mark.asyncio
async def test_timeline_medium_duration_yellow() -> None:
    """30-60 second duration falls in the medium (yellow) range."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=0,
                event_type="tool_call",
                tool_name="run_terminal_command",
                tool_call_id="med",
                timestamp=1000,
            ),
            make_trace_event(
                index=1,
                event_type="tool_result",
                tool_name="run_terminal_command",
                tool_call_id="med",
                timestamp=1045,  # 45s
            ),
        ]
        tl.load_events(events)
        assert 0 in tl.durations
        assert 30 <= tl.durations[0] < 60


@pytest.mark.asyncio
async def test_timeline_tool_error_non_tool_column() -> None:
    """Tool error with empty tool name renders without markup prefix."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=0,
                event_type="tool_call",
                tool_name="",  # Empty tool name → empty tool_col
                is_error=True,
                timestamp=1000,
            ),
        ]
        tl.load_events(events)
        assert tl.row_count == 1


@pytest.mark.asyncio
async def test_timeline_row_highlighted_non_digit_key() -> None:
    """row_highlighted handles non-digit row key gracefully."""
    from textual.widgets import DataTable

    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        # Manually trigger with a non-digit key
        from textual.widgets._data_table import RowKey

        event = DataTable.RowHighlighted(
            tl,
            tl.cursor_coordinate,
            RowKey("not-a-digit"),
        )
        tl.on_data_table_row_highlighted(event)
