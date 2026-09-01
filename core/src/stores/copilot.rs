//! Copilot session-store.db plus events.jsonl.

use crate::event::{Event, EventType, ListMeta, SessionLocator};
use crate::jsonl;
use crate::store::Store;
use crate::text;
use rusqlite::Connection;
use serde_json::Value;
use std::path::{Path, PathBuf};

pub struct Copilot;

fn events_path(db: &Path, sid: &str) -> PathBuf {
    db.parent()
        .unwrap_or(db)
        .join("session-state")
        .join(sid)
        .join("events.jsonl")
}

fn from_row(row: &crate::jsonl::JsonlRow) -> Option<Event> {
    let typ = text::field_str(&row.value, "type");
    let data = row.value.get("data").cloned().unwrap_or(Value::Null);
    let ts = text::field_i64(&row.value, "timestamp");
    let eid = text::field_str(&row.value, "id");
    let ev = match typ.as_str() {
        "session.start" | "assistant.turn_start" => Event::new(EventType::TurnStarted)
            .with_ts(ts)
            .with_raw(&row.raw),
        "assistant.turn_end" => Event::new(EventType::TurnEnded)
            .with_ts(ts)
            .with_raw(&row.raw),
        "session.shutdown" => Event::new(EventType::TurnCompleted)
            .with_ts(ts)
            .with_content(text::field_str(&data, "shutdownType"))
            .with_raw(&row.raw),
        "user.message" => Event::new(EventType::UserMessageChunk)
            .with_ts(ts)
            .with_content(text::text_of(data.get("content").unwrap_or(&Value::Null)))
            .with_raw(&row.raw),
        "assistant.message" => Event::new(EventType::AgentMessageChunk)
            .with_ts(ts)
            .with_content(text::text_of(data.get("content").unwrap_or(&Value::Null)))
            .with_raw(&row.raw),
        "tool.execution_start" => {
            let args = data.get("arguments").cloned().unwrap_or(Value::Null);
            let mut ev = Event::new(EventType::ToolCall)
                .with_ts(ts)
                .with_raw(serde_json::to_string(&args).unwrap_or_else(|_| row.raw.clone()));
            ev.tool_name = text::field_str(&data, "toolName");
            ev.tool_call_id = {
                let id = text::field_str(&data, "toolCallId");
                if id.is_empty() {
                    eid
                } else {
                    id
                }
            };
            ev
        }
        "tool.execution_complete" => {
            let mut ev = Event::new(EventType::ToolCallUpdate)
                .with_ts(ts)
                .with_content(text::text_of(data.get("result").unwrap_or(&Value::Null)))
                .with_raw(&row.raw);
            ev.tool_call_id = text::field_str(&data, "toolCallId");
            ev.is_error = data.get("success") != Some(&Value::Bool(true));
            ev
        }
        "subagent.started" => {
            let mut ev = Event::new(EventType::SubagentSpawned)
                .with_ts(ts)
                .with_content(text::field_str(&data, "agentName"))
                .with_raw(&row.raw);
            ev.child_session_id = text::field_str(&row.value, "agentId");
            if ev.child_session_id.is_empty() {
                ev.child_session_id = text::field_str(&data, "agentId");
            }
            ev.subagent_type = text::field_str(&data, "agentType");
            ev.description = text::field_str(&data, "agentDescription");
            ev
        }
        "subagent.completed" => {
            let mut ev = Event::new(EventType::SubagentFinished)
                .with_ts(ts)
                .with_raw(&row.raw);
            ev.child_session_id = text::field_str(&row.value, "agentId");
            if ev.child_session_id.is_empty() {
                ev.child_session_id = text::field_str(&data, "agentId");
            }
            ev.subagent_type = text::field_str(&data, "agentType");
            ev
        }
        _ => return None,
    };
    Some(ev)
}

impl Store for Copilot {
    fn id(&self) -> &'static str {
        "copilot"
    }

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator> {
        let mut out = Vec::new();
        for raw in roots {
            let db = if raw.is_file() {
                raw.clone()
            } else {
                raw.join("session-store.db")
            };
            if !db.is_file() {
                continue;
            }
            let Ok(con) =
                Connection::open_with_flags(&db, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            else {
                continue;
            };
            let Ok(mut stmt) = con.prepare("SELECT id, cwd FROM sessions") else {
                continue;
            };
            let rows = stmt.query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1).unwrap_or_default(),
                ))
            });
            let Ok(rows) = rows else { continue };
            for row in rows.flatten() {
                out.push(SessionLocator {
                    harness: "copilot".into(),
                    session_id: row.0,
                    locator: db.clone(),
                    cwd: row.1,
                });
            }
        }
        out
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        Ok(ListMeta {
            session_id: session_id.to_string(),
            locator: locator.to_path_buf(),
            harness: "copilot".into(),
            model_id: "unknown".into(),
            ..ListMeta::default()
        })
    }

    fn timeline(&self, locator: &Path, session_id: &str) -> Result<Vec<Event>, String> {
        let path = if locator.extension().and_then(|s| s.to_str()) == Some("jsonl") {
            locator.to_path_buf()
        } else {
            events_path(locator, session_id)
        };
        if !path.is_file() {
            return Ok(Vec::new());
        }
        let mut events: Vec<Event> = jsonl::read_objects(&path)
            .into_iter()
            .filter_map(|row| from_row(&row))
            .collect();
        text::index_events(&mut events);
        Ok(events)
    }
}
