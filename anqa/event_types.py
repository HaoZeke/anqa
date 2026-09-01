"""Timeline event types (1:1 with harness signals).

``TraceEvent.event_type`` uses stored names from:

* ``updates.jsonl`` → ``params.update.sessionUpdate``
* ``events.jsonl`` → top-level ``type`` (turn markers / errors)

``system`` is injected ``system_prompt.txt`` chrome.
"""

from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    """Stored timeline type. Values match harness signal names."""

    USER_MESSAGE_CHUNK = "user_message_chunk"
    AGENT_MESSAGE_CHUNK = "agent_message_chunk"
    AGENT_THOUGHT_CHUNK = "agent_thought_chunk"
    TOOL_CALL = "tool_call"
    TOOL_CALL_UPDATE = "tool_call_update"
    PLAN = "plan"
    TASK_BACKGROUNDED = "task_backgrounded"
    TASK_COMPLETED = "task_completed"
    SCHEDULED_TASK_CREATED = "scheduled_task_created"
    SCHEDULED_TASK_UPDATED = "scheduled_task_updated"
    SCHEDULED_TASK_FIRED = "scheduled_task_fired"
    SCHEDULED_TASK_DELETED = "scheduled_task_deleted"
    TURN_COMPLETED = "turn_completed"
    SUBAGENT_SPAWNED = "subagent_spawned"
    SUBAGENT_FINISHED = "subagent_finished"
    CURRENT_MODE_UPDATE = "current_mode_update"
    RETRY_STATE = "retry_state"
    GOAL_UPDATED = "goal_updated"
    SESSION_RECAP = "session_recap"
    AUTO_COMPACT_STARTED = "auto_compact_started"
    AUTO_COMPACT_COMPLETED = "auto_compact_completed"
    COMPACTION_CHECKPOINT = "compaction_checkpoint"
    HOOK_EXECUTION = "hook_execution"
    HOOK_ANNOTATION = "hook_annotation"
    TURN_STARTED = "turn_started"
    TURN_ENDED = "turn_ended"
    SESSION_ERROR = "session_error"
    ERROR = "error"
    TURN_ERROR = "turn_error"
    FATAL_ERROR = "fatal_error"
    SYSTEM = "system"


class EventKind(StrEnum):
    """Coarse role for color and layout."""

    USER = "user"
    AGENT = "agent"
    THOUGHT = "thought"
    TOOL = "tool"
    TOOL_RESULT = "tool_result"
    PLAN = "plan"
    ERROR = "error"
    SESSION = "session"
    SYSTEM = "system"
    SUBAGENT = "subagent"
    TASK = "task"
    OTHER = "other"


USER_MESSAGE_CHUNK = EventType.USER_MESSAGE_CHUNK
AGENT_MESSAGE_CHUNK = EventType.AGENT_MESSAGE_CHUNK
AGENT_THOUGHT_CHUNK = EventType.AGENT_THOUGHT_CHUNK
TOOL_CALL = EventType.TOOL_CALL
TOOL_CALL_UPDATE = EventType.TOOL_CALL_UPDATE
PLAN = EventType.PLAN
TASK_BACKGROUNDED = EventType.TASK_BACKGROUNDED
TASK_COMPLETED = EventType.TASK_COMPLETED
SCHEDULED_TASK_CREATED = EventType.SCHEDULED_TASK_CREATED
SCHEDULED_TASK_UPDATED = EventType.SCHEDULED_TASK_UPDATED
SCHEDULED_TASK_FIRED = EventType.SCHEDULED_TASK_FIRED
SCHEDULED_TASK_DELETED = EventType.SCHEDULED_TASK_DELETED
TURN_COMPLETED = EventType.TURN_COMPLETED
SUBAGENT_SPAWNED = EventType.SUBAGENT_SPAWNED
SUBAGENT_FINISHED = EventType.SUBAGENT_FINISHED
CURRENT_MODE_UPDATE = EventType.CURRENT_MODE_UPDATE
RETRY_STATE = EventType.RETRY_STATE
GOAL_UPDATED = EventType.GOAL_UPDATED
SESSION_RECAP = EventType.SESSION_RECAP
AUTO_COMPACT_STARTED = EventType.AUTO_COMPACT_STARTED
AUTO_COMPACT_COMPLETED = EventType.AUTO_COMPACT_COMPLETED
COMPACTION_CHECKPOINT = EventType.COMPACTION_CHECKPOINT
HOOK_EXECUTION = EventType.HOOK_EXECUTION
HOOK_ANNOTATION = EventType.HOOK_ANNOTATION
TURN_STARTED = EventType.TURN_STARTED
TURN_ENDED = EventType.TURN_ENDED
SESSION_ERROR = EventType.SESSION_ERROR
ERROR = EventType.ERROR
TURN_ERROR = EventType.TURN_ERROR
FATAL_ERROR = EventType.FATAL_ERROR
SYSTEM = EventType.SYSTEM

# Sets for filters / stats / segmentation
USER_TYPES = frozenset({USER_MESSAGE_CHUNK})
AGENT_TYPES = frozenset({AGENT_MESSAGE_CHUNK})
THOUGHT_TYPES = frozenset({AGENT_THOUGHT_CHUNK})
MESSAGE_TYPES = USER_TYPES | AGENT_TYPES | THOUGHT_TYPES
TOOL_CALL_TYPES = frozenset({TOOL_CALL})
TOOL_UPDATE_TYPES = frozenset({TOOL_CALL_UPDATE})
TOOL_TYPES = TOOL_CALL_TYPES | TOOL_UPDATE_TYPES
PLAN_TYPES = frozenset({PLAN})
SCHEDULED_TASK_TYPES = frozenset(
    {
        SCHEDULED_TASK_CREATED,
        SCHEDULED_TASK_UPDATED,
        SCHEDULED_TASK_FIRED,
        SCHEDULED_TASK_DELETED,
    }
)
TASK_TYPES = frozenset({TASK_BACKGROUNDED, TASK_COMPLETED}) | SCHEDULED_TASK_TYPES
SUBAGENT_TYPES = frozenset({SUBAGENT_SPAWNED, SUBAGENT_FINISHED})
TURN_BOUNDARY_TYPES = frozenset({TURN_STARTED, TURN_ENDED, TURN_COMPLETED})
TURN_STARTED_TYPES = frozenset({TURN_STARTED})
TURN_ENDED_TYPES = frozenset({TURN_ENDED})
ERROR_TYPES = frozenset({SESSION_ERROR, ERROR, TURN_ERROR, FATAL_ERROR})
MODE_TYPES = frozenset({CURRENT_MODE_UPDATE, RETRY_STATE})
GOAL_TYPES = frozenset({GOAL_UPDATED})
RECAP_TYPES = frozenset({SESSION_RECAP})
COMPACT_TYPES = frozenset({AUTO_COMPACT_STARTED, AUTO_COMPACT_COMPLETED, COMPACTION_CHECKPOINT})
HOOK_TYPES = frozenset({HOOK_EXECUTION, HOOK_ANNOTATION})
# Session chrome in the Turn / Session filter
SESSION_CHROME_TYPES = (
    TURN_BOUNDARY_TYPES
    | ERROR_TYPES
    | MODE_TYPES
    | GOAL_TYPES
    | RECAP_TYPES
    | COMPACT_TYPES
    | HOOK_TYPES
    | frozenset({SYSTEM})
)

# sessionUpdate values we materialize as timeline rows (1:1 identity map).
SESSION_UPDATE_TIMELINE_TYPES = frozenset(
    {
        USER_MESSAGE_CHUNK,
        AGENT_MESSAGE_CHUNK,
        AGENT_THOUGHT_CHUNK,
        TOOL_CALL,
        TOOL_CALL_UPDATE,
        PLAN,
        TASK_BACKGROUNDED,
        TASK_COMPLETED,
        SCHEDULED_TASK_CREATED,
        SCHEDULED_TASK_UPDATED,
        SCHEDULED_TASK_FIRED,
        SCHEDULED_TASK_DELETED,
        TURN_COMPLETED,
        SUBAGENT_SPAWNED,
        SUBAGENT_FINISHED,
        CURRENT_MODE_UPDATE,
        RETRY_STATE,
        GOAL_UPDATED,
        SESSION_RECAP,
        AUTO_COMPACT_STARTED,
        AUTO_COMPACT_COMPLETED,
        COMPACTION_CHECKPOINT,
        HOOK_EXECUTION,
        HOOK_ANNOTATION,
    }
)


def type_label(event_type: str) -> str:
    """Display label: stored identifier with underscores → spaces."""
    et = (event_type or "").strip()
    if not et:
        return "?"
    return et.replace("_", " ")


def job_event_label(event_type: str, *, kind: str = "") -> str:
    """Honest timeline words for task / schedule bookends (not “subagent”)."""
    et = (event_type or "").strip()
    monitor = kind == "monitor"
    if et == TASK_BACKGROUNDED:
        return "monitor" if monitor else "background start"
    if et == TASK_COMPLETED:
        return "monitor done" if monitor else "background done"
    if et == SCHEDULED_TASK_CREATED:
        return "schedule created"
    if et == SCHEDULED_TASK_UPDATED:
        return "schedule updated"
    if et == SCHEDULED_TASK_FIRED:
        return "schedule fired"
    if et == SCHEDULED_TASK_DELETED:
        return "schedule deleted"
    if et.startswith("scheduled_task_"):
        return et.replace("_", " ")
    return ""


def event_kind(event_type: str) -> EventKind:
    """Coarse role for UI color and layout."""
    et = (event_type or "").strip()
    if et in USER_TYPES or et == EventKind.USER:
        return EventKind.USER
    if et in AGENT_TYPES or et == "assistant":
        return EventKind.AGENT
    if et in THOUGHT_TYPES or et == EventKind.THOUGHT:
        return EventKind.THOUGHT
    if et in TOOL_CALL_TYPES:
        return EventKind.TOOL
    if et in TOOL_UPDATE_TYPES or et == EventKind.TOOL_RESULT:
        return EventKind.TOOL_RESULT
    if et in PLAN_TYPES:
        return EventKind.PLAN
    if et in ERROR_TYPES:
        return EventKind.ERROR
    if et in SESSION_CHROME_TYPES - ERROR_TYPES - {SYSTEM} or et == EventKind.SESSION:
        return EventKind.SESSION
    if et == SYSTEM:
        return EventKind.SYSTEM
    if et in SUBAGENT_TYPES or et == EventKind.SUBAGENT:
        return EventKind.SUBAGENT
    if et in TASK_TYPES or et.startswith("scheduled_task_"):
        return EventKind.TASK
    return EventKind.OTHER
