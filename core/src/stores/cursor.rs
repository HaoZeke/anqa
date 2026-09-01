//! Cursor agent-transcript jsonl.

use crate::event::{Event, EventType, ListMeta, SessionLocator};
use crate::jsonl::{self, JsonlRow};
use crate::store::Store;
use crate::text;
use serde_json::Value;
use std::path::{Path, PathBuf};

pub struct Cursor;

fn is_transcript(path: &Path) -> bool {
    path.is_file()
        && path.extension().and_then(|s| s.to_str()) == Some("jsonl")
        && path
            .parent()
            .and_then(|p| p.parent())
            .and_then(|p| p.file_name())
            .and_then(|n| n.to_str())
            == Some("agent-transcripts")
}

fn collect(roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for raw in roots {
        if is_transcript(raw) {
            out.push(raw.clone());
            continue;
        }
        if raw.is_dir() {
            out.extend(
                crate::walk::find_files(raw, ".jsonl", "")
                    .into_iter()
                    .filter(|p| p.components().any(|c| c.as_os_str() == "agent-transcripts")),
            );
        }
    }
    out
}

fn blocks_text(content: &Value) -> String {
    text::text_of(content)
}

fn user_query(text_in: &str) -> String {
    if let Some(start) = text_in.find("<user_query>") {
        if let Some(end) = text_in[start..].find("</user_query>") {
            return text_in[start + 12..start + end].trim().to_string();
        }
    }
    text_in.to_string()
}

fn from_row(row: &JsonlRow) -> Vec<Event> {
    let typ = text::field_str(&row.value, "type");
    if typ == "turn_ended" {
        let status = text::field_str(&row.value, "status");
        let ended = matches!(
            status.to_ascii_lowercase().as_str(),
            "cancelled" | "canceled" | "aborted" | "interrupted" | "error"
        );
        return vec![Event::new(if ended {
            EventType::TurnEnded
        } else {
            EventType::TurnCompleted
        })
        .with_content(status)
        .with_raw(&row.raw)];
    }
    let role = text::field_str(&row.value, "role");
    if role.is_empty() {
        return Vec::new();
    }
    let content = row
        .value
        .pointer("/message/content")
        .cloned()
        .unwrap_or(Value::Null);
    let mut events = Vec::new();
    if role == "user" {
        events.push(
            Event::new(EventType::UserMessageChunk)
                .with_content(user_query(&blocks_text(&content)))
                .with_raw(&row.raw),
        );
        return events;
    }
    if role != "assistant" {
        return events;
    }
    let text_body = blocks_text(&content);
    if !text_body.is_empty() {
        events.push(
            Event::new(EventType::AgentMessageChunk)
                .with_content(text_body)
                .with_raw(&row.raw),
        );
    }
    if let Some(parts) = content.as_array() {
        for part in parts {
            if text::field_str(part, "type") != "tool_use" {
                continue;
            }
            let name = text::field_str(part, "name");
            let input = part.get("input").cloned().unwrap_or(Value::Null);
            let mut ev = Event::new(EventType::ToolCall)
                .with_raw(serde_json::to_string(&input).unwrap_or_default());
            ev.tool_name = name;
            ev.tool_call_id = text::field_str(part, "id");
            events.push(ev);
        }
    }
    events
}

impl Store for Cursor {
    fn id(&self) -> &'static str {
        "cursor"
    }

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator> {
        collect(roots)
            .into_iter()
            .filter_map(|file| {
                let sid = file.file_stem()?.to_str()?.to_string();
                Some(SessionLocator {
                    harness: "cursor".into(),
                    session_id: sid,
                    locator: file,
                    cwd: String::new(),
                })
            })
            .collect()
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_file() {
            return Err(format!("cursor session not found: {session_id}"));
        }
        Ok(ListMeta {
            session_id: session_id.to_string(),
            locator: locator.to_path_buf(),
            harness: "cursor".into(),
            model_id: "unknown".into(),
            ..ListMeta::default()
        })
    }

    fn timeline(&self, locator: &Path, session_id: &str) -> Result<Vec<Event>, String> {
        if !locator.is_file() {
            return Err(format!("cursor session not found: {session_id}"));
        }
        let mut events = Vec::new();
        for row in jsonl::read_objects(locator) {
            events.extend(from_row(&row));
        }
        text::index_events(&mut events);
        Ok(events)
    }
}
