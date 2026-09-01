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
            "subagent_spawned" => Self::SubagentSpawned,
            "subagent_finished" => Self::SubagentFinished,
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
