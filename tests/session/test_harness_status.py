"""Shared last-signal list status for every adapter."""

from __future__ import annotations

import pytest
from anqa.harness.status import from_last
from anqa.models import ListStatus


@pytest.mark.parametrize(
    ("token", "want"),
    [
        ("turn_completed", ListStatus.COMPLETE),
        ("task_complete", ListStatus.COMPLETE),
        ("end_turn", ListStatus.COMPLETE),
        ("session.shutdown", ListStatus.COMPLETE),
        ("done", ListStatus.COMPLETE),
        ("turn_aborted", ListStatus.CANCELLED),
        ("killed", ListStatus.CANCELLED),
        ("error", ListStatus.CANCELLED),
        ("running", ListStatus.RUNNING),
        ("in_progress", ListStatus.RUNNING),
        ("pending", ListStatus.RUNNING),
        ("executing", ListStatus.RUNNING),
        ("not_fully_idle", ListStatus.RUNNING),
        ("task_started", ListStatus.IDLE),
        ("assistant.turn_start", ListStatus.IDLE),
        ("tool.execution_start", ListStatus.IDLE),
        ("subagent.started", ListStatus.IDLE),
        ("tool_use", ListStatus.IDLE),
        ("toolUse", ListStatus.IDLE),
        ("user", ListStatus.IDLE),
        ("user_message", ListStatus.IDLE),
        ("user_message_chunk", ListStatus.IDLE),
        ("turn_started", ListStatus.IDLE),
        ("assistant", ListStatus.IDLE),
        ("assistant.message", ListStatus.IDLE),
        ("", ListStatus.IDLE),
    ],
)
def test_from_last_maps_lifecycle_not_content(token: str, want: ListStatus) -> None:
    assert from_last(token) is want
