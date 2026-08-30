"""Last store signal → list turn outcome. Every adapter uses this table."""

from __future__ import annotations

_COMPLETE = frozenset(
    {
        "complete",
        "completed",
        "success",
        "ok",
        "done",
        "end_turn",
        "stop",
        "stop_sequence",
        "task_complete",
        "turn_completed",
        "turn_ended",
        "session_recap",
        "session.shutdown",
        "assistant.turn_end",
    }
)
_CANCELLED = frozenset(
    {
        "cancelled",
        "canceled",
        "error",
        "failed",
        "failure",
        "killed",
        "aborted",
        "interrupted",
        "timeout",
        "turn_aborted",
        "max_tokens",
        "refusal",
    }
)
_RUNNING = frozenset(
    {
        "running",
        "in_progress",
        "pending",
        "active",
        "task_started",
        "assistant.turn_start",
        "tool.execution_start",
        "subagent.started",
        "tool_use",
        "tooluse",
        "not_fully_idle",
    }
)


def from_last(token: str) -> str:
    """Map one last store signal to ``running``, ``complete``, ``cancelled``, or ``""``.

    Content rows (a user message, a bare assistant blob) are not a status.
    Lifecycle closes and explicit in-progress flags are.

    :param token: Store-specific last signal, already chosen by the adapter.
    :returns: List ``turn_outcome`` fragment.
    """
    key = (token or "").strip().lower().replace(" ", "_")
    if key in _COMPLETE:
        return "complete"
    if key in _CANCELLED:
        return "cancelled"
    if key in _RUNNING:
        return "running"
    return ""
