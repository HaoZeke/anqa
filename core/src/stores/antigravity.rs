//! Antigravity conversation db + transcript jsonl.

use crate::event::{Event, EventType, ListMeta, ListStatus, SessionLocator};
use crate::jsonl;
use crate::store::Store;
use crate::text;
use rusqlite::Connection;
use serde_json::Value;
use std::path::{Path, PathBuf};

pub struct Antigravity;

fn transcript_path(root: &Path, sid: &str) -> PathBuf {
    let brain = root
        .join("brain")
        .join(sid)
        .join(".system_generated")
        .join("logs");
    let full = brain.join("transcript_full.jsonl");
    if full.is_file() {
        return full;
    }
    brain.join("transcript.jsonl")
}

fn conversation_db(root: &Path, sid: &str) -> PathBuf {
    root.join("conversations").join(format!("{sid}.db"))
}

fn tag_body(raw: &str, tag: &str) -> String {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    if let Some(start) = raw.find(&open) {
        if let Some(end) = raw[start + open.len()..].find(&close) {
            return raw[start + open.len()..start + open.len() + end]
                .trim()
                .to_string();
        }
    }
    String::new()
}

fn timeline_rows(rows: &[crate::jsonl::JsonlRow]) -> Vec<Event> {
    let mut events = Vec::new();
    let mut turn = 0i32;
    let mut last_tool = String::new();
    for row in rows {
        let typ = text::field_str(&row.value, "type");
        let ts = text::field_i64(&row.value, "created_at");
        if typ == "USER_INPUT" {
            let raw = text::field_str(&row.value, "content");
            let request = tag_body(&raw, "USER_REQUEST");
            if !request.is_empty() {
                let mut start = Event::new(EventType::TurnStarted)
                    .with_ts(ts)
                    .with_content(format!("turn_number={turn}"))
                    .with_raw(&row.raw);
                start.turn_number = Some(turn);
                events.push(start);
                events.push(
                    Event::new(EventType::UserMessageChunk)
                        .with_ts(ts)
                        .with_content(request)
                        .with_raw(&row.raw),
                );
                turn += 1;
            }
            let plan = tag_body(&raw, "USER_PLAN");
            if !plan.is_empty() {
                events.push(
                    Event::new(EventType::Plan)
                        .with_ts(ts)
                        .with_content(plan)
                        .with_raw(&row.raw),
                );
            }
            continue;
        }
        if typ == "PLANNER_RESPONSE" {
            let thinking = text::field_str(&row.value, "thinking");
            if !thinking.trim().is_empty() {
                events.push(
                    Event::new(EventType::AgentThoughtChunk)
                        .with_ts(ts)
                        .with_content(thinking)
                        .with_raw(&row.raw),
                );
            }
            if let Some(calls) = row.value.get("tool_calls").and_then(|v| v.as_array()) {
                for call in calls {
                    let name = text::field_str(call, "name");
                    last_tool = name.clone();
                    let args = call.get("args").cloned().unwrap_or(Value::Null);
                    let mut ev = Event::new(EventType::ToolCall)
                        .with_ts(ts)
                        .with_raw(serde_json::to_string(&args).unwrap_or_else(|_| row.raw.clone()));
                    ev.tool_name = name;
                    events.push(ev);
                }
            }
            let content = text::field_str(&row.value, "content");
            if !content.trim().is_empty() {
                events.push(
                    Event::new(EventType::AgentMessageChunk)
                        .with_ts(ts)
                        .with_content(content)
                        .with_raw(&row.raw),
                );
            }
            continue;
        }
        if typ == "GENERIC" {
            let mut ev = Event::new(EventType::ToolCallUpdate)
                .with_ts(ts)
                .with_content(text::field_str(&row.value, "content"))
                .with_raw(&row.raw);
            ev.tool_name = last_tool.clone();
            events.push(ev);
            continue;
        }
        if typ == "SYSTEM_MESSAGE" {
            let content = text::field_str(&row.value, "content");
            if !content.trim().is_empty() {
                events.push(
                    Event::new(EventType::System)
                        .with_ts(ts)
                        .with_content(content)
                        .with_raw(&row.raw),
                );
            }
        }
    }
    events
}

impl Store for Antigravity {
    fn id(&self) -> &'static str {
        "antigravity"
    }

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator> {
        let mut out = Vec::new();
        for raw in roots {
            let brain = if raw.file_name().and_then(|n| n.to_str()) == Some("brain") {
                raw.clone()
            } else {
                raw.join("brain")
            };
            if !brain.is_dir() {
                continue;
            }
            if let Ok(entries) = std::fs::read_dir(&brain) {
                for entry in entries.flatten() {
                    let sid = entry.file_name().to_string_lossy().into_owned();
                    let t = transcript_path(raw, &sid);
                    if t.is_file() || entry.path().is_dir() {
                        out.push(SessionLocator {
                            harness: "antigravity".into(),
                            session_id: sid,
                            locator: raw.clone(),
                            cwd: String::new(),
                        });
                    }
                }
            }
        }
        out
    }

    fn records(
        &self,
        locator: &Path,
        session_id: &str,
    ) -> Result<Vec<crate::store::Record>, String> {
        let path = if locator.extension().and_then(|s| s.to_str()) == Some("jsonl") {
            locator.to_path_buf()
        } else {
            transcript_path(locator, session_id)
        };
        if !path.is_file() {
            return Ok(Vec::new());
        }
        Ok(jsonl::cached_records(&path, None))
    }

    fn events(&self, records: &[crate::store::Record]) -> Vec<Event> {
        timeline_rows(records)
    }

    fn stamp(&self, locator: &Path, session_id: &str) -> crate::event::FileStamp {
        if locator.extension().and_then(|s| s.to_str()) == Some("jsonl") {
            return jsonl::file_stamp(locator);
        }
        jsonl::pair_stamp(
            &transcript_path(locator, session_id),
            &conversation_db(locator, session_id),
        )
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        let db = if locator.extension().and_then(|s| s.to_str()) == Some("db") {
            locator.to_path_buf()
        } else {
            conversation_db(&store_root(locator), session_id)
        };
        if !db.is_file() {
            return Err(format!("antigravity session not found: {session_id}"));
        }
        let root = store_root(&db);
        Ok(meta_for(&root, &db, session_id))
    }
}

fn store_root(locator: &Path) -> PathBuf {
    if locator.is_file() && locator.extension().and_then(|s| s.to_str()) == Some("db") {
        if locator
            .parent()
            .and_then(|p| p.file_name())
            .and_then(|n| n.to_str())
            == Some("conversations")
        {
            return locator
                .parent()
                .and_then(|p| p.parent())
                .unwrap_or(locator)
                .to_path_buf();
        }
        return locator.parent().unwrap_or(locator).to_path_buf();
    }
    locator.to_path_buf()
}

fn summaries_db(root: &Path) -> PathBuf {
    root.join("conversation_summaries.db")
}

fn open_ro(path: &Path) -> Option<Connection> {
    Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).ok()
}

fn load_summary(root: &Path, session_id: &str) -> Value {
    let db = summaries_db(root);
    let Some(con) = open_ro(&db) else {
        return Value::Null;
    };
    let row = con.query_row(
        "SELECT conversation_id, title, preview, step_count, last_modified_time, \
         workspace_uris, status, source, project_id, agent_name, \
         parent_conversation_id, nesting_depth, not_fully_idle, killed, \
         last_user_input_time FROM conversation_summaries WHERE conversation_id = ?1",
        [session_id],
        |r| {
            let mut map = serde_json::Map::new();
            let cols = [
                "conversation_id",
                "title",
                "preview",
                "step_count",
                "last_modified_time",
                "workspace_uris",
                "status",
                "source",
                "project_id",
                "agent_name",
                "parent_conversation_id",
                "nesting_depth",
                "not_fully_idle",
                "killed",
                "last_user_input_time",
            ];
            for (i, name) in cols.iter().enumerate() {
                let val = r.get_ref(i)?;
                map.insert((*name).into(), sql_json(val));
            }
            Ok(Value::Object(map))
        },
    );
    row.unwrap_or(Value::Null)
}

fn sql_json(val: rusqlite::types::ValueRef<'_>) -> Value {
    match val {
        rusqlite::types::ValueRef::Null => Value::Null,
        rusqlite::types::ValueRef::Integer(n) => Value::from(n),
        rusqlite::types::ValueRef::Real(n) => Value::from(n),
        rusqlite::types::ValueRef::Text(s) => {
            Value::String(String::from_utf8_lossy(s).into_owned())
        }
        rusqlite::types::ValueRef::Blob(b) => {
            Value::String(String::from_utf8_lossy(b).into_owned())
        }
    }
}

fn child_count(root: &Path, session_id: &str) -> u32 {
    let Some(con) = open_ro(&summaries_db(root)) else {
        return 0;
    };
    con.query_row(
        "SELECT COUNT(*) FROM conversation_summaries WHERE parent_conversation_id = ?1",
        [session_id],
        |r| r.get::<_, i64>(0),
    )
    .unwrap_or(0) as u32
}

fn cwd_from_uris(raw: &Value) -> String {
    let text = match raw {
        Value::String(s) => s.clone(),
        _ => text::as_str(raw),
    };
    if text.is_empty() {
        return String::new();
    }
    let val = serde_json::from_str::<Value>(&text)
        .unwrap_or(Value::Array(vec![Value::String(text.clone())]));
    let Some(items) = val.as_array() else {
        return String::new();
    };
    for item in items {
        let uri = text::as_str(item);
        if let Some(path) = uri.strip_prefix("file://") {
            return path.to_string();
        }
        if uri.starts_with('/') {
            return uri;
        }
    }
    String::new()
}

fn cwd_from_last_conversations(root: &Path, session_id: &str) -> String {
    let path = root.join("cache").join("last_conversations.json");
    let Ok(text) = std::fs::read_to_string(path) else {
        return String::new();
    };
    let Ok(val) = serde_json::from_str::<Value>(&text) else {
        return String::new();
    };
    let Some(obj) = val.as_object() else {
        return String::new();
    };
    for (key, val) in obj {
        if text::as_str(val) == session_id {
            return key.clone();
        }
    }
    String::new()
}

fn gemini_model_in(blob: &[u8]) -> Option<String> {
    let needle = b"gemini-";
    let mut found = None;
    let mut start = 0;
    while let Some(pos) = memchr::memmem::find(&blob[start..], needle) {
        let at = start + pos;
        let rest = &blob[at + needle.len()..];
        let mut end = 0;
        for (j, b) in rest.iter().enumerate() {
            if b.is_ascii_alphanumeric() || *b == b'.' || *b == b'-' {
                end = j + 1;
            } else {
                break;
            }
        }
        if end > 0 {
            if let Ok(s) = std::str::from_utf8(&blob[at..at + needle.len() + end]) {
                found = Some(s.to_string());
            }
        }
        start = at + 1;
    }
    found
}

fn model_from_conversation_db(db: &Path) -> String {
    let Some(con) = open_ro(db) else {
        return String::new();
    };
    let tables = [
        ("executor_metadata", "data"),
        ("gen_metadata", "data"),
        ("steps", "step_payload"),
    ];
    let mut found = String::new();
    for (table, col) in tables {
        let Ok(mut stmt) = con.prepare(&format!("SELECT {col} FROM {table}")) else {
            continue;
        };
        let Ok(rows) = stmt.query_map([], |row| {
            Ok(match row.get_ref(0)? {
                rusqlite::types::ValueRef::Blob(b) => b.to_vec(),
                rusqlite::types::ValueRef::Text(s) => s.to_vec(),
                _ => Vec::new(),
            })
        }) else {
            continue;
        };
        for row in rows.flatten() {
            if let Some(model) = gemini_model_in(&row) {
                found = model;
            }
        }
    }
    found
}

fn first_user_title(rows: &[crate::jsonl::JsonlRow], summary: &Value) -> String {
    let title = text::field_str(summary, "title");
    if !title.is_empty() {
        return text::first_line(&title, 80);
    }
    for row in rows {
        if text::field_str(&row.value, "type") != "USER_INPUT" {
            continue;
        }
        let text_body = tag_body(&text::field_str(&row.value, "content"), "USER_REQUEST");
        if !text_body.is_empty() {
            return text::first_line(&text_body, 80);
        }
    }
    String::new()
}

fn truthy(val: &Value) -> bool {
    match val {
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_i64().unwrap_or(0) != 0,
        Value::String(s) => matches!(s.as_str(), "1" | "true" | "True"),
        _ => false,
    }
}

fn turn_outcome(rows: &[crate::jsonl::JsonlRow], summary: &Value) -> String {
    if truthy(summary.get("killed").unwrap_or(&Value::Null)) {
        return ListStatus::Cancelled.as_str().into();
    }
    if truthy(summary.get("not_fully_idle").unwrap_or(&Value::Null)) {
        return ListStatus::Running.as_str().into();
    }
    let mut last: Option<&crate::jsonl::JsonlRow> = None;
    for row in rows {
        let typ = text::field_str(&row.value, "type");
        if matches!(
            typ.as_str(),
            "USER_INPUT" | "PLANNER_RESPONSE" | "GENERIC" | "SYSTEM_MESSAGE"
        ) {
            last = Some(row);
        }
    }
    let Some(last) = last else {
        return String::new();
    };
    let mapped = ListStatus::from_token(&text::field_str(&last.value, "status"));
    if mapped != ListStatus::Idle {
        mapped.as_str().into()
    } else {
        String::new()
    }
}

fn count_tools(rows: &[crate::jsonl::JsonlRow]) -> u32 {
    let mut n = 0u32;
    for row in rows {
        if let Some(calls) = row.value.get("tool_calls").and_then(|v| v.as_array()) {
            n += calls.len() as u32;
        }
    }
    n
}

fn meta_for(root: &Path, db: &Path, session_id: &str) -> ListMeta {
    let rows = jsonl::window(&transcript_path(root, session_id));
    let summary = load_summary(root, session_id);
    let mut created = String::new();
    let mut updated = text::field_iso(&summary, "last_modified_time");
    if updated.is_empty() {
        updated = text::field_iso(&summary, "last_user_input_time");
    }
    if let Some(first) = rows.first() {
        created = text::field_iso(&first.value, "created_at");
    }
    if let Some(last) = rows.last() {
        let last_ts = text::field_iso(&last.value, "created_at");
        if !last_ts.is_empty() {
            updated = last_ts;
        }
    }
    if created.is_empty() {
        created = updated.clone();
    }
    let cwd = {
        let from_uris = cwd_from_uris(summary.get("workspace_uris").unwrap_or(&Value::Null));
        if from_uris.is_empty() {
            cwd_from_last_conversations(root, session_id)
        } else {
            from_uris
        }
    };
    let kids = child_count(root, session_id);
    let model = {
        let name = text::field_str(&summary, "agent_name");
        if !name.is_empty() {
            name
        } else {
            let found = model_from_conversation_db(db);
            if found.is_empty() {
                "unknown".into()
            } else {
                found
            }
        }
    };
    ListMeta {
        session_id: session_id.to_string(),
        locator: db.to_path_buf(),
        model_id: model,
        title: first_user_title(&rows, &summary),
        created_at: created.clone(),
        updated_at: updated.clone(),
        duration_seconds: text::duration_secs(
            text::epoch_secs(&Value::String(created)),
            text::epoch_secs(&Value::String(updated)),
        ),
        tool_call_count: count_tools(&rows),
        turn_outcome: turn_outcome(&rows, &summary),
        harness: "antigravity".into(),
        harness_version: String::new(),
        run_dir: cwd,
        num_events: 0,
        has_subagents: kids > 0,
        subagent_count: kids,
        context_tokens_used: None,
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::io::Write;
    use std::path::{Path, PathBuf};
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_dir(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "anqa-antigravity-{label}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write_transcript(root: &Path, sid: &str, line: &str) -> PathBuf {
        let path = root
            .join("brain")
            .join(sid)
            .join(".system_generated")
            .join("logs")
            .join("transcript.jsonl");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, format!("{line}\n")).unwrap();
        path
    }

    #[test]
    fn list_meta_window_title_and_last_turn() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../tests/fixtures/harness/antigravity/antigravity-cli");
        let meta =
            crate::list_meta("antigravity", &root, "aaaaaaaa-1111-4111-8111-000000000001").unwrap();
        assert_eq!(meta.title, "Reply with AGY_PROBE_OK");
        assert_eq!(meta.turn_outcome, "complete");
    }

    #[test]
    fn antigravity_stamp_follows_that_session_transcript() {
        let root = temp_dir("stamp");
        write_transcript(&root, "sid-a", r#"{"type":"USER_INPUT","content":"a1"}"#);
        write_transcript(
            &root,
            "sid-b",
            r#"{"type":"USER_INPUT","content":"session-b-longer"}"#,
        );
        fs::create_dir_all(root.join("conversations")).unwrap();
        fs::write(root.join("conversations").join("sid-a.db"), b"db-a").unwrap();
        fs::write(root.join("conversations").join("sid-b.db"), b"db-bbbb").unwrap();

        let stamp_a = crate::stamp("antigravity", &root, "sid-a").unwrap();
        let stamp_b = crate::stamp("antigravity", &root, "sid-b").unwrap();
        assert_ne!(stamp_a, stamp_b);

        let mut b = fs::OpenOptions::new()
            .append(true)
            .open(
                root.join("brain")
                    .join("sid-b")
                    .join(".system_generated")
                    .join("logs")
                    .join("transcript.jsonl"),
            )
            .unwrap();
        writeln!(b, r#"{{"type":"USER_INPUT","content":"b2"}}"#).unwrap();
        drop(b);

        assert_eq!(
            crate::stamp("antigravity", &root, "sid-a").unwrap(),
            stamp_a,
            "growing B must not change A"
        );
        assert_ne!(
            crate::stamp("antigravity", &root, "sid-b").unwrap(),
            stamp_b
        );

        fs::write(root.join("conversation_summaries.db"), b"shared").unwrap();
        assert_eq!(
            crate::stamp("antigravity", &root, "sid-a").unwrap(),
            stamp_a,
            "shared store files must not be the stamp"
        );

        let mut a = fs::OpenOptions::new()
            .append(true)
            .open(
                root.join("brain")
                    .join("sid-a")
                    .join(".system_generated")
                    .join("logs")
                    .join("transcript.jsonl"),
            )
            .unwrap();
        writeln!(a, r#"{{"type":"USER_INPUT","content":"a2"}}"#).unwrap();
        drop(a);
        assert_ne!(
            crate::stamp("antigravity", &root, "sid-a").unwrap(),
            stamp_a
        );

        let _ = fs::remove_dir_all(&root);
    }
}
