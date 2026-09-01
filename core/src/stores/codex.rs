//! Codex rollout jsonl.

use crate::event::{Event, EventType, ListMeta, SessionLocator};
use crate::jsonl::{self, JsonlRow};
use crate::store::Store;
use crate::text;
use serde_json::Value;
use std::path::{Path, PathBuf};

pub struct Codex;

fn collect(roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for raw in roots {
        if raw.is_file()
            && raw
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("rollout-") && n.ends_with(".jsonl"))
        {
            out.push(raw.clone());
        } else if raw.is_dir() {
            out.extend(crate::walk::find_files(raw, ".jsonl", "rollout-"));
        }
    }
    out
}

fn sid_of(path: &Path) -> String {
    jsonl::first_object(path)
        .and_then(|row| {
            let pl = row.value.get("payload").cloned().unwrap_or(Value::Null);
            let id = text::field_str(&pl, "id");
            if !id.is_empty() {
                return Some(id);
            }
            let id = text::field_str(&pl, "session_id");
            if !id.is_empty() {
                Some(id)
            } else {
                None
            }
        })
        .unwrap_or_default()
}

fn is_env_context(text: &str) -> bool {
    text.trim_start().starts_with("<environment_context>")
}

fn blocks_text(content: &Value, kind: &str) -> String {
    let Some(items) = content.as_array() else {
        return text::text_of(content);
    };
    let mut bits = Vec::new();
    for item in items {
        if text::field_str(item, "type") == kind {
            bits.push(text::field_str(item, "text"));
        }
    }
    bits.join("\n")
}

fn from_row(row: &JsonlRow) -> Vec<Event> {
    let typ = text::field_str(&row.value, "type");
    let ts = text::field_i64(&row.value, "timestamp");
    let pl = row.value.get("payload").cloned().unwrap_or(Value::Null);
    if typ == "event_msg" {
        let kind = text::field_str(&pl, "type");
        if kind == "task_started" {
            return vec![Event::new(EventType::TurnStarted)
                .with_ts(ts)
                .with_raw(&row.raw)];
        }
        if kind == "task_complete" {
            return vec![Event::new(EventType::TurnCompleted)
                .with_ts(ts)
                .with_raw(&row.raw)];
        }
        if kind == "turn_aborted" {
            return vec![Event::new(EventType::TurnEnded)
                .with_ts(ts)
                .with_content(text::field_str(&pl, "reason"))
                .with_raw(&row.raw)];
        }
        if kind == "item_completed" {
            let item = pl.get("item").cloned().unwrap_or(Value::Null);
            if text::field_str(&item, "type") == "SubAgentActivity" {
                return subagent_item(&item, ts, &row.raw);
            }
        }
        return Vec::new();
    }
    if typ != "response_item" {
        return Vec::new();
    }
    let kind = text::field_str(&pl, "type");
    if kind == "message" {
        let role = text::field_str(&pl, "role");
        if role == "user" {
            let body = blocks_text(pl.get("content").unwrap_or(&Value::Null), "input_text");
            if is_env_context(&body) {
                return Vec::new();
            }
            return vec![Event::new(EventType::UserMessageChunk)
                .with_ts(ts)
                .with_content(body)
                .with_raw(&row.raw)];
        }
        if role == "assistant" {
            let body = blocks_text(pl.get("content").unwrap_or(&Value::Null), "output_text");
            return vec![Event::new(EventType::AgentMessageChunk)
                .with_ts(ts)
                .with_content(body)
                .with_raw(&row.raw)];
        }
    }
    if kind == "custom_tool_call" || kind == "function_call" {
        let name = text::field_str(&pl, "name");
        let input = pl.get("input").cloned().unwrap_or(Value::Null);
        let raw_args = if input.is_string() {
            serde_json::to_string(&serde_json::json!({"command": text::as_str(&input)}))
                .unwrap_or_default()
        } else {
            serde_json::to_string(&input).unwrap_or_default()
        };
        let mut ev = Event::new(EventType::ToolCall)
            .with_ts(ts)
            .with_raw(raw_args);
        ev.tool_name = name;
        ev.tool_call_id = {
            let a = text::field_str(&pl, "call_id");
            if a.is_empty() {
                text::field_str(&pl, "id")
            } else {
                a
            }
        };
        return vec![ev];
    }
    if kind == "custom_tool_call_output" || kind == "function_call_output" {
        let mut ev = Event::new(EventType::ToolCallUpdate)
            .with_ts(ts)
            .with_content(text::text_of(pl.get("output").unwrap_or(&Value::Null)))
            .with_raw(&row.raw);
        ev.tool_call_id = text::field_str(&pl, "call_id");
        return vec![ev];
    }
    Vec::new()
}

fn subagent_item(item: &Value, ts: Option<i64>, raw: &str) -> Vec<Event> {
    let kind = text::field_str(item, "kind");
    let child = text::field_str(item, "agent_thread_id");
    let path = text::field_str(item, "agent_path");
    let typ = path.rsplit('/').next().unwrap_or("").to_string();
    if kind == "started" {
        let mut ev = Event::new(EventType::SubagentSpawned)
            .with_ts(ts)
            .with_content(if path.is_empty() {
                typ.clone()
            } else {
                path.clone()
            })
            .with_raw(raw);
        ev.child_session_id = child;
        ev.subagent_type = typ;
        ev.description = path;
        return vec![ev];
    }
    if kind == "completed" || kind == "interrupted" {
        let mut ev = Event::new(EventType::SubagentFinished)
            .with_ts(ts)
            .with_content(if path.is_empty() { typ } else { path })
            .with_raw(raw);
        ev.child_session_id = child;
        return vec![ev];
    }
    Vec::new()
}

impl Store for Codex {
    fn id(&self) -> &'static str {
        "codex"
    }

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator> {
        collect(roots)
            .into_iter()
            .filter_map(|file| {
                let sid = sid_of(&file);
                if sid.is_empty() {
                    return None;
                }
                Some(SessionLocator {
                    harness: "codex".into(),
                    session_id: sid,
                    locator: file,
                    cwd: String::new(),
                })
            })
            .collect()
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_file() {
            return Err(format!("codex session not found: {session_id}"));
        }
        Ok(ListMeta {
            session_id: session_id.to_string(),
            locator: locator.to_path_buf(),
            harness: "codex".into(),
            model_id: "unknown".into(),
            ..ListMeta::default()
        })
    }

    fn timeline(&self, locator: &Path, session_id: &str) -> Result<Vec<Event>, String> {
        if !locator.is_file() {
            return Err(format!("codex session not found: {session_id}"));
        }
        let mut events = Vec::new();
        for row in jsonl::read_objects(locator) {
            events.extend(from_row(&row));
        }
        text::index_events(&mut events);
        Ok(events)
    }
}
