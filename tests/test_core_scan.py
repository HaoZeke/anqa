"""updates.jsonl keep/skip (same results with or without anqa._scan)."""

from __future__ import annotations

from pathlib import Path

from anqa.scan import (
    filter_updates,
    keep_updates_line,
    scan_forced_off,
    using_scan,
)

FIXTURE = Path(__file__).parent / "fixtures" / "snapshots" / "minimal_session" / "updates.jsonl"

USER = b'{"params":{"update":{"sessionUpdate":"user_message_chunk","content":"hi"}}}'
STREAMING = (
    b'{"params":{"update":{"sessionUpdate":"tool_call_update","content":"' + (b"x" * 256) + b'"}}}'
)
COMPLETED = b'{"params":{"update":{"sessionUpdate":"tool_call_update","status":"completed"}}}'
COMPLETED_SPACED = (
    b'{"params":{"update":{"sessionUpdate":"tool_call_update","status": "completed"}}}'
)
FAILED = b'{"params":{"update":{"sessionUpdate":"tool_call_update","status":"failed"}}}'
FAILED_SPACED = b'{"params":{"update":{"sessionUpdate":"tool_call_update","status": "failed"}}}'
IS_ERROR = b'{"params":{"update":{"sessionUpdate":"tool_call_update","isError":true}}}'
IS_ERROR_SPACED = b'{"params":{"update":{"sessionUpdate":"tool_call_update","isError": true}}}'


def test_skip_non_terminal_tool_call_update() -> None:
    assert keep_updates_line(STREAMING) is False


def test_keep_terminal_completed_failed_is_error() -> None:
    for line in (
        COMPLETED,
        COMPLETED_SPACED,
        FAILED,
        FAILED_SPACED,
        IS_ERROR,
        IS_ERROR_SPACED,
    ):
        assert keep_updates_line(line) is True


def test_keep_user_message_chunk() -> None:
    assert keep_updates_line(USER) is True


def test_keep_empty() -> None:
    assert keep_updates_line(b"") is True


def test_filter_drops_fat_streaming_keeps_two_others() -> None:
    blob = USER + b"\n" + STREAMING + b"\n" + COMPLETED + b"\n"
    assert filter_updates(blob) == [USER, COMPLETED]


def test_filter_strips_cr_and_keeps_incomplete_last_line() -> None:
    blob = USER + b"\r\n" + STREAMING + b"\r\n" + COMPLETED
    assert filter_updates(blob) == [USER, COMPLETED]


def test_filter_incomplete_streaming_dropped() -> None:
    assert filter_updates(USER + b"\n" + STREAMING) == [USER]


def test_filter_empty_input() -> None:
    assert filter_updates(b"") == []


def test_filter_fixture_matches_linewise_keep() -> None:
    blob = FIXTURE.read_bytes() + STREAMING + b"\n" + USER + b"\n"
    kept = filter_updates(blob)
    expected = [line for line in blob.split(b"\n") if line and keep_updates_line(line)]
    assert kept == expected


def test_scan_flag_is_consistent() -> None:
    if scan_forced_off():
        assert using_scan() is False
    else:
        import anqa._core as ext

        assert using_scan() is True
        assert ext.keep_updates_line(STREAMING) is False
        assert ext.keep_updates_line(COMPLETED) is True
