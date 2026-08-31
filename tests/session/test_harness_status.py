"""Shared last-signal list status for every adapter."""

from __future__ import annotations

import pytest
from anqa.harness.status import from_last


@pytest.mark.parametrize(
    ("token", "want"),
    [
        ("turn_completed", "complete"),
        ("task_complete", "complete"),
        ("end_turn", "complete"),
        ("session.shutdown", "complete"),
        ("done", "complete"),
        ("turn_aborted", "cancelled"),
        ("killed", "cancelled"),
        ("error", "cancelled"),
        ("running", "running"),
        ("in_progress", "running"),
        ("pending", "running"),
        ("executing", "running"),
        ("not_fully_idle", "running"),
        ("task_started", ""),
        ("assistant.turn_start", ""),
        ("tool.execution_start", ""),
        ("subagent.started", ""),
        ("tool_use", ""),
        ("toolUse", ""),
        ("user", ""),
        ("user_message", ""),
        ("user_message_chunk", ""),
        ("turn_started", ""),
        ("assistant", ""),
        ("assistant.message", ""),
        ("", ""),
    ],
)
def test_from_last_maps_lifecycle_not_content(token: str, want: str) -> None:
    assert from_last(token) == want
