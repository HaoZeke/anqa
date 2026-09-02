//! Cursor agent-transcript jsonl.

use crate::event::{Event, EventType, ListMeta, ListStatus, SessionLocator};
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

    fn records(
        &self,
        locator: &Path,
        session_id: &str,
    ) -> Result<Vec<crate::store::Record>, String> {
        crate::store::jsonl_records(locator, self.id(), session_id)
    }

    fn events(&self, records: &[crate::store::Record]) -> Vec<Event> {
        records.iter().flat_map(from_row).collect()
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_file() {
            return Err(format!("cursor session not found: {session_id}"));
        }
        let rows = jsonl::window(locator);
        let header = find_meta(locator, session_id);
        Ok(meta_from_window(&rows, locator, session_id, &header))
    }
}

fn load_json(path: &Path) -> Value {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null)
}

fn sid_meta_in(chats: &Path, sid: &str) -> Option<Value> {
    let Ok(entries) = std::fs::read_dir(chats) else {
        return None;
    };
    for entry in entries.flatten() {
        let meta = entry.path().join(sid).join("meta.json");
        if meta.is_file() {
            return Some(load_json(&meta));
        }
    }
    None
}

fn find_meta(locator: &Path, sid: &str) -> Value {
    for ancestor in locator.ancestors() {
        if let Some(v) = sid_meta_in(&ancestor.join("chats"), sid) {
            return v;
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        if let Some(v) = sid_meta_in(&PathBuf::from(home).join(".cursor").join("chats"), sid) {
            return v;
        }
    }
    Value::Null
}

fn model_name_in(blob: &str) -> Option<String> {
    let needle = "\"modelName\"";
    let mut rest = blob;
    let mut found = None;
    while let Some(i) = rest.find(needle) {
        let after = rest[i + needle.len()..].trim_start();
        if let Some(after) = after.strip_prefix(':') {
            let after = after.trim_start();
            if let Some(after) = after.strip_prefix('"') {
                if let Some(end) = after.find('"') {
                    let name = after[..end].trim();
                    if !name.is_empty() {
                        found = Some(name.to_string());
                    }
                }
            }
        }
        rest = &rest[i + needle.len()..];
    }
    found
}

fn model_from_store(locator: &Path, sid: &str) -> String {
    let mut roots = Vec::new();
    for ancestor in locator.ancestors() {
        roots.push(ancestor.join("chats"));
    }
    if let Ok(home) = std::env::var("HOME") {
        roots.push(PathBuf::from(home).join(".cursor").join("chats"));
    }
    let mut found = String::new();
    for chats in roots {
        let Ok(entries) = std::fs::read_dir(&chats) else {
            continue;
        };
        for entry in entries.flatten() {
            let db = entry.path().join(sid).join("store.db");
            if !db.is_file() {
                continue;
            }
            let Ok(con) = rusqlite::Connection::open_with_flags(
                &db,
                rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
            ) else {
                continue;
            };
            let Ok(mut stmt) = con.prepare("SELECT data FROM blobs") else {
                continue;
            };
            let Ok(rows) = stmt.query_map([], |row| row.get::<_, Vec<u8>>(0)) else {
                continue;
            };
            for row in rows.flatten() {
                let text = String::from_utf8_lossy(&row);
                if let Some(name) = model_name_in(&text) {
                    found = name;
                }
            }
        }
    }
    found
}

fn first_user_title(rows: &[JsonlRow]) -> String {
    for row in rows {
        if text::field_str(&row.value, "role") != "user" {
            continue;
        }
        let content = row
            .value
            .pointer("/message/content")
            .cloned()
            .unwrap_or(Value::Null);
        let text_body = user_query(&blocks_text(&content));
        if !text_body.is_empty() {
            return text::first_line(&text_body, 120);
        }
    }
    String::new()
}

fn session_title(header: &Value, rows: &[JsonlRow]) -> String {
    let prompt = first_user_title(rows);
    if !prompt.is_empty() {
        return prompt;
    }
    let raw = user_query(&text::field_str(header, "title"));
    text::first_line(&raw, 120)
}

fn count_tools(rows: &[JsonlRow]) -> u32 {
    let mut n = 0u32;
    for row in rows {
        let content = row
            .value
            .pointer("/message/content")
            .cloned()
            .unwrap_or(Value::Null);
        let Some(parts) = content.as_array() else {
            continue;
        };
        n += parts
            .iter()
            .filter(|p| text::field_str(p, "type") == "tool_use")
            .count() as u32;
    }
    n
}

fn last_signal(rows: &[JsonlRow]) -> (String, String) {
    let mut last_type = String::new();
    let mut last_status = String::new();
    for row in rows {
        let typ = text::field_str(&row.value, "type");
        if typ == "turn_ended" {
            last_type = typ;
            last_status = text::field_str(&row.value, "status");
            continue;
        }
        let role = text::field_str(&row.value, "role");
        if role == "user" || role == "assistant" {
            last_type.clear();
            last_status = role;
        }
    }
    (last_type, last_status)
}

fn turn_outcome(rows: &[JsonlRow]) -> String {
    let (kind, status) = last_signal(rows);
    if kind == "turn_ended" {
        let mapped = ListStatus::from_token(&status);
        if mapped == ListStatus::Idle {
            return ListStatus::Complete.as_str().into();
        }
        return mapped.as_str().into();
    }
    ListStatus::from_token(&status).as_str().into()
}

fn meta_from_window(rows: &[JsonlRow], path: &Path, sid: &str, header: &Value) -> ListMeta {
    let created = text::field_iso(header, "createdAtMs");
    let mut updated = text::field_iso(header, "updatedAtMs");
    if updated.is_empty() {
        updated = created.clone();
    }
    let model = model_from_store(path, sid);
    ListMeta {
        session_id: sid.to_string(),
        locator: path.to_path_buf(),
        model_id: if model.is_empty() {
            "unknown".into()
        } else {
            model
        },
        title: session_title(header, rows),
        created_at: created.clone(),
        updated_at: updated.clone(),
        duration_seconds: text::duration_secs(
            text::epoch_secs(&header.get("createdAtMs").cloned().unwrap_or(Value::Null)),
            text::epoch_secs(&header.get("updatedAtMs").cloned().unwrap_or(Value::Null)),
        ),
        tool_call_count: count_tools(rows),
        turn_outcome: turn_outcome(rows),
        harness: "cursor".into(),
        harness_version: String::new(),
        run_dir: text::field_str(header, "cwd"),
        num_events: 0,
        has_subagents: false,
        subagent_count: 0,
        context_tokens_used: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn list_meta_window_title_and_last_turn() {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(
            "../tests/fixtures/harness/cursor/projects/tmp-cursor-probe/agent-transcripts/aaaaaaaa-1111-4111-8111-000000000001/aaaaaaaa-1111-4111-8111-000000000001.jsonl",
        );
        let meta = Cursor
            .list_meta(&path, "aaaaaaaa-1111-4111-8111-000000000001")
            .unwrap();
        assert_eq!(meta.title, "Reply with CURSOR_PROBE_OK");
        assert_eq!(meta.turn_outcome, "complete");
        assert_eq!(meta.run_dir, "/tmp/cursor-probe-ws");
    }
}
