//! Copilot session-store.db plus events.jsonl.

use crate::event::{Event, EventType, ListMeta, ListStatus, SessionLocator};
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

    fn records(
        &self,
        locator: &Path,
        session_id: &str,
    ) -> Result<Vec<crate::store::Record>, String> {
        let path = if locator.extension().and_then(|s| s.to_str()) == Some("jsonl") {
            locator.to_path_buf()
        } else {
            events_path(locator, session_id)
        };
        if !path.is_file() {
            return Ok(Vec::new());
        }
        Ok(jsonl::cached_records(&path, None))
    }

    fn events(&self, records: &[crate::store::Record]) -> Vec<Event> {
        records.iter().filter_map(from_row).collect()
    }

    fn stamp(&self, locator: &Path, session_id: &str) -> crate::event::FileStamp {
        let path = if locator.extension().and_then(|s| s.to_str()) == Some("jsonl") {
            locator.to_path_buf()
        } else {
            events_path(locator, session_id)
        };
        jsonl::file_stamp(&path)
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if session_id.is_empty() {
            return Err("copilot session id is required".into());
        }
        if !locator.is_file() {
            return Err(format!("copilot database not found: {}", locator.display()));
        }
        let con = Connection::open_with_flags(locator, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .map_err(|e| e.to_string())?;
        let row = con
            .query_row(
                "SELECT id, cwd, summary, created_at, updated_at FROM sessions WHERE id = ?1",
                [session_id],
                |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1).unwrap_or_default(),
                        r.get::<_, String>(2).unwrap_or_default(),
                        r.get::<_, rusqlite::types::Value>(3)?,
                        r.get::<_, rusqlite::types::Value>(4)?,
                    ))
                },
            )
            .map_err(|_| format!("copilot session not found: {session_id}"))?;
        let events = jsonl::window(&events_path(locator, session_id));
        Ok(meta_from_row(locator, &row, &events))
    }
}

const TURN_SIGNALS: &[&str] = &[
    "assistant.turn_start",
    "tool.execution_start",
    "subagent.started",
    "session.shutdown",
    "assistant.turn_end",
];

fn sql_stamp(val: &rusqlite::types::Value) -> String {
    match val {
        rusqlite::types::Value::Text(s) => text::iso_stamp(&Value::String(s.clone())),
        rusqlite::types::Value::Integer(n) => text::iso_millis(*n),
        rusqlite::types::Value::Real(n) => text::iso_millis(*n as i64),
        _ => String::new(),
    }
}

fn sql_epoch(val: &rusqlite::types::Value) -> Option<i64> {
    match val {
        rusqlite::types::Value::Text(s) => text::epoch_secs(&Value::String(s.clone())),
        rusqlite::types::Value::Integer(n) => text::epoch_secs(&Value::Number((*n).into())),
        rusqlite::types::Value::Real(n) => text::epoch_secs(&Value::from(*n)),
        _ => None,
    }
}

fn last_turn_type(events: &[crate::jsonl::JsonlRow]) -> String {
    let mut last = String::new();
    for ev in events {
        let typ = text::field_str(&ev.value, "type");
        if TURN_SIGNALS.contains(&typ.as_str()) {
            last = typ;
        }
    }
    last
}

fn model_from_events(events: &[crate::jsonl::JsonlRow]) -> String {
    for row in events.iter().rev() {
        let typ = text::field_str(&row.value, "type");
        let data = row.value.get("data").cloned().unwrap_or(Value::Null);
        if matches!(
            typ.as_str(),
            "assistant.message" | "tool.execution_start" | "session.shutdown"
        ) {
            let mid = text::field_str(&data, "model");
            if !mid.is_empty() {
                return mid;
            }
            let mid = text::field_str(&data, "currentModel");
            if !mid.is_empty() {
                return mid;
            }
        }
    }
    String::new()
}

fn version_from_events(events: &[crate::jsonl::JsonlRow]) -> String {
    for row in events {
        if text::field_str(&row.value, "type") != "session.start" {
            continue;
        }
        let ver = text::field_str(
            &row.value.get("data").cloned().unwrap_or(Value::Null),
            "copilotVersion",
        );
        if !ver.is_empty() {
            return ver;
        }
    }
    String::new()
}

fn count_type(events: &[crate::jsonl::JsonlRow], want: &str) -> u32 {
    events
        .iter()
        .filter(|row| text::field_str(&row.value, "type") == want)
        .count() as u32
}

fn meta_from_row(
    db: &Path,
    row: &(
        String,
        String,
        String,
        rusqlite::types::Value,
        rusqlite::types::Value,
    ),
    events: &[crate::jsonl::JsonlRow],
) -> ListMeta {
    let (sid, cwd, summary, created_raw, updated_raw) = row;
    let created = sql_stamp(created_raw);
    let mut updated = sql_stamp(updated_raw);
    if updated.is_empty() {
        updated = created.clone();
    }
    let kids = count_type(events, "subagent.started");
    let model = model_from_events(events);
    ListMeta {
        session_id: sid.clone(),
        locator: db.to_path_buf(),
        model_id: if model.is_empty() {
            "unknown".into()
        } else {
            model
        },
        title: summary.trim().to_string(),
        created_at: created,
        updated_at: updated,
        duration_seconds: text::duration_secs(sql_epoch(created_raw), sql_epoch(updated_raw)),
        tool_call_count: count_type(events, "tool.execution_start"),
        turn_outcome: {
            let mapped = ListStatus::from_token(&last_turn_type(events));
            if mapped != ListStatus::Idle {
                mapped.as_str().into()
            } else {
                String::new()
            }
        },
        harness: "copilot".into(),
        harness_version: version_from_events(events),
        run_dir: cwd.trim().to_string(),
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
            "anqa-copilot-{label}-{}-{}",
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

    fn write_events(root: &Path, sid: &str, line: &str) -> PathBuf {
        let path = root.join("session-state").join(sid).join("events.jsonl");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, format!("{line}\n")).unwrap();
        path
    }

    #[test]
    fn list_meta_window_title_and_last_turn() {
        let db = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../tests/fixtures/harness/copilot/session-store.db");
        let meta =
            crate::list_meta("copilot", &db, "aaaaaaaa-1111-4111-8111-000000000001").unwrap();
        assert_eq!(meta.title, "Reply with COPILOT_PROBE_OK");
        assert_eq!(meta.turn_outcome, "complete");
        assert_eq!(meta.model_id, "gpt-5-mini");
    }

    #[test]
    fn copilot_stamp_follows_that_session_events_file() {
        let dir = temp_dir("stamp");
        let db = dir.join("session-store.db");
        fs::write(&db, b"shared").unwrap();
        write_events(&dir, "sid-a", r#"{"type":"user.message","id":"a1"}"#);
        write_events(
            &dir,
            "sid-b",
            r#"{"type":"user.message","id":"session-b-longer"}"#,
        );

        let stamp_a = crate::stamp("copilot", &db, "sid-a").unwrap();
        let stamp_b = crate::stamp("copilot", &db, "sid-b").unwrap();
        assert_ne!(stamp_a, stamp_b);

        let mut b = fs::OpenOptions::new()
            .append(true)
            .open(dir.join("session-state").join("sid-b").join("events.jsonl"))
            .unwrap();
        writeln!(b, r#"{{"type":"user.message","id":"b2"}}"#).unwrap();
        drop(b);

        assert_eq!(
            crate::stamp("copilot", &db, "sid-a").unwrap(),
            stamp_a,
            "growing B must not change A"
        );
        assert_ne!(crate::stamp("copilot", &db, "sid-b").unwrap(), stamp_b);

        fs::write(&db, b"shared-and-grown").unwrap();
        assert_eq!(
            crate::stamp("copilot", &db, "sid-a").unwrap(),
            stamp_a,
            "shared db must not be the stamp"
        );

        let mut a = fs::OpenOptions::new()
            .append(true)
            .open(dir.join("session-state").join("sid-a").join("events.jsonl"))
            .unwrap();
        writeln!(a, r#"{{"type":"user.message","id":"a2"}}"#).unwrap();
        drop(a);
        assert_ne!(crate::stamp("copilot", &db, "sid-a").unwrap(), stamp_a);

        let _ = fs::remove_dir_all(&dir);
    }
}
