//! Typed session event and list meta. A raw record is the original line.

use serde_json::Value;
use std::path::PathBuf;

/// Stored timeline type. Values match anqa event names.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum EventType {
    UserMessageChunk,
    AgentMessageChunk,
    AgentThoughtChunk,
    ToolCall,
    ToolCallUpdate,
    Plan,
    TaskBackgrounded,
    TaskCompleted,
    ScheduledTaskCreated,
    ScheduledTaskUpdated,
    ScheduledTaskFired,
    ScheduledTaskDeleted,
    TurnCompleted,
    SubagentSpawned,
    SubagentFinished,
    CurrentModeUpdate,
    RetryState,
    GoalUpdated,
    SessionRecap,
    AutoCompactStarted,
    AutoCompactCompleted,
    CompactionCheckpoint,
    HookExecution,
    HookAnnotation,
    TurnStarted,
    TurnEnded,
    SessionError,
    Error,
    TurnError,
    FatalError,
    System,
    Other(String),
}

impl EventType {
    #[must_use]
    pub fn as_str(&self) -> &str {
        match self {
            Self::UserMessageChunk => "user_message_chunk",
            Self::AgentMessageChunk => "agent_message_chunk",
            Self::AgentThoughtChunk => "agent_thought_chunk",
            Self::ToolCall => "tool_call",
            Self::ToolCallUpdate => "tool_call_update",
            Self::Plan => "plan",
            Self::TaskBackgrounded => "task_backgrounded",
            Self::TaskCompleted => "task_completed",
            Self::ScheduledTaskCreated => "scheduled_task_created",
            Self::ScheduledTaskUpdated => "scheduled_task_updated",
            Self::ScheduledTaskFired => "scheduled_task_fired",
            Self::ScheduledTaskDeleted => "scheduled_task_deleted",
            Self::TurnCompleted => "turn_completed",
            Self::SubagentSpawned => "subagent_spawned",
            Self::SubagentFinished => "subagent_finished",
            Self::CurrentModeUpdate => "current_mode_update",
            Self::RetryState => "retry_state",
            Self::GoalUpdated => "goal_updated",
            Self::SessionRecap => "session_recap",
            Self::AutoCompactStarted => "auto_compact_started",
            Self::AutoCompactCompleted => "auto_compact_completed",
            Self::CompactionCheckpoint => "compaction_checkpoint",
            Self::HookExecution => "hook_execution",
            Self::HookAnnotation => "hook_annotation",
            Self::TurnStarted => "turn_started",
            Self::TurnEnded => "turn_ended",
            Self::SessionError => "session_error",
            Self::Error => "error",
            Self::TurnError => "turn_error",
            Self::FatalError => "fatal_error",
            Self::System => "system",
            Self::Other(name) => name.as_str(),
        }
    }

    #[must_use]
    pub fn parse(name: &str) -> Self {
        match name {
            "user_message_chunk" => Self::UserMessageChunk,
            "agent_message_chunk" => Self::AgentMessageChunk,
            "agent_thought_chunk" => Self::AgentThoughtChunk,
            "tool_call" => Self::ToolCall,
            "tool_call_update" => Self::ToolCallUpdate,
            "plan" => Self::Plan,
            "task_backgrounded" => Self::TaskBackgrounded,
            "task_completed" => Self::TaskCompleted,
            "scheduled_task_created" => Self::ScheduledTaskCreated,
            "scheduled_task_updated" => Self::ScheduledTaskUpdated,
            "scheduled_task_fired" => Self::ScheduledTaskFired,
            "scheduled_task_deleted" => Self::ScheduledTaskDeleted,
            "turn_completed" => Self::TurnCompleted,
            "subagent_spawned" | "subagent_started" => Self::SubagentSpawned,
            "subagent_finished" | "subagent_completed" => Self::SubagentFinished,
            "current_mode_update" => Self::CurrentModeUpdate,
            "retry_state" => Self::RetryState,
            "goal_updated" => Self::GoalUpdated,
            "session_recap" => Self::SessionRecap,
            "auto_compact_started" => Self::AutoCompactStarted,
            "auto_compact_completed" => Self::AutoCompactCompleted,
            "compaction_checkpoint" => Self::CompactionCheckpoint,
            "hook_execution" => Self::HookExecution,
            "hook_annotation" => Self::HookAnnotation,
            "turn_started" => Self::TurnStarted,
            "turn_ended" => Self::TurnEnded,
            "session_error" => Self::SessionError,
            "error" => Self::Error,
            "turn_error" => Self::TurnError,
            "fatal_error" => Self::FatalError,
            "system" => Self::System,
            other => Self::Other(other.to_string()),
        }
    }

    #[must_use]
    pub fn is_message_chunk(&self) -> bool {
        matches!(
            self,
            Self::UserMessageChunk | Self::AgentMessageChunk | Self::AgentThoughtChunk
        )
    }

    #[must_use]
    pub fn is_scheduled_task(&self) -> bool {
        matches!(
            self,
            Self::ScheduledTaskCreated
                | Self::ScheduledTaskUpdated
                | Self::ScheduledTaskFired
                | Self::ScheduledTaskDeleted
        )
    }

    /// ``events.jsonl`` rows that belong on the timeline (turn bookends / errors).
    #[must_use]
    pub fn is_turn_marker(&self) -> bool {
        matches!(
            self,
            Self::TurnStarted
                | Self::TurnEnded
                | Self::SessionError
                | Self::Error
                | Self::TurnError
                | Self::FatalError
        )
    }
}

/// One timeline row. `raw` is the original store record as text.
#[derive(Clone, Debug)]
pub struct Event {
    pub index: u32,
    pub event_type: EventType,
    pub timestamp: Option<i64>,
    pub content: String,
    pub raw: String,
    pub tool_name: String,
    pub tool_call_id: String,
    pub is_error: bool,
    pub update_index: u32,
    pub prompt_index: Option<i32>,
    pub turn_number: Option<i32>,
    pub child_session_id: String,
    pub subagent_type: String,
    pub description: String,
}

impl Event {
    #[must_use]
    pub fn new(event_type: EventType) -> Self {
        Self {
            index: 0,
            event_type,
            timestamp: None,
            content: String::new(),
            raw: String::new(),
            tool_name: String::new(),
            tool_call_id: String::new(),
            is_error: false,
            update_index: 0,
            prompt_index: None,
            turn_number: None,
            child_session_id: String::new(),
            subagent_type: String::new(),
            description: String::new(),
        }
    }

    #[must_use]
    pub fn with_raw(mut self, raw: impl AsRef<str>) -> Self {
        self.raw = raw.as_ref().to_string();
        self
    }

    #[must_use]
    pub fn with_content(mut self, content: impl AsRef<str>) -> Self {
        self.content = content.as_ref().to_string();
        self
    }

    #[must_use]
    pub fn with_ts(mut self, ts: Option<i64>) -> Self {
        self.timestamp = ts;
        self
    }

    #[must_use]
    pub fn tool_args(&self) -> Option<Value> {
        let val: Value = serde_json::from_str(&self.raw).ok()?;
        val.as_object().cloned().map(Value::Object)
    }

    /// Copy each numbered ``turn_started`` onto later rows.
    ///
    /// Rows before the first start inherit that start. A session with no
    /// numbered starts stamps ``0`` on every row.
    pub fn carry_turn_numbers(events: &mut [Self]) {
        let has_numbered_start = events
            .iter()
            .any(|ev| matches!(ev.event_type, EventType::TurnStarted) && ev.turn_number.is_some());
        if !has_numbered_start {
            for ev in events.iter_mut() {
                ev.turn_number = Some(0);
            }
            return;
        }
        let mut current: Option<i32> = None;
        let mut pending: Vec<usize> = Vec::new();
        for i in 0..events.len() {
            if matches!(events[i].event_type, EventType::TurnStarted) {
                if let Some(n) = events[i].turn_number {
                    current = Some(n);
                    for j in pending.drain(..) {
                        events[j].turn_number = Some(n);
                    }
                }
            }
            if let Some(n) = current {
                events[i].turn_number = Some(n);
            } else {
                pending.push(i);
            }
        }
    }
}

/// List-grade session stamp.
#[derive(Clone, Debug, Default)]
pub struct ListMeta {
    pub session_id: String,
    pub locator: PathBuf,
    pub model_id: String,
    pub title: String,
    pub created_at: String,
    pub updated_at: String,
    pub duration_seconds: f64,
    pub tool_call_count: u32,
    pub turn_outcome: String,
    pub harness: String,
    pub harness_version: String,
    pub run_dir: String,
    pub num_events: u32,
    pub has_subagents: bool,
    pub subagent_count: u32,
}

impl ListMeta {
    /// Empty list row for *session_id* at *locator*.
    #[must_use]
    pub fn for_session(harness: &str, locator: &std::path::Path, session_id: &str) -> Self {
        Self {
            session_id: session_id.to_string(),
            locator: locator.to_path_buf(),
            harness: harness.to_string(),
            model_id: "unknown".into(),
            ..Self::default()
        }
    }
}

/// Cheap file stamp (mtime, size, extra, extra).
pub type FileStamp = (f64, u64, u64, u64);

/// One discovered session.
#[derive(Clone, Debug)]
pub struct SessionLocator {
    pub harness: String,
    pub session_id: String,
    pub locator: PathBuf,
    pub cwd: String,
}

#[cfg(test)]
mod tests {
    use super::{Event, EventType};

    #[test]
    fn carry_turn_numbers_stamps_zero_without_starts() {
        let mut evs = vec![
            Event::new(EventType::UserMessageChunk),
            Event::new(EventType::AgentMessageChunk),
        ];
        Event::carry_turn_numbers(&mut evs);
        assert_eq!(evs[0].turn_number, Some(0));
        assert_eq!(evs[1].turn_number, Some(0));
    }

    #[test]
    fn carry_turn_numbers_backfills_then_forwards() {
        let mut start = Event::new(EventType::TurnStarted);
        start.turn_number = Some(3);
        let mut evs = vec![
            Event::new(EventType::UserMessageChunk),
            start,
            Event::new(EventType::AgentMessageChunk),
        ];
        Event::carry_turn_numbers(&mut evs);
        assert_eq!(evs[0].turn_number, Some(3));
        assert_eq!(evs[1].turn_number, Some(3));
        assert_eq!(evs[2].turn_number, Some(3));
    }
}
