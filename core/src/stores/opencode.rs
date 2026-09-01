//! OpenCode sqlite session store.

use crate::event::{Event, EventType, SessionLocator};
use crate::store::Store;
use crate::text;
use rusqlite::Connection;
use serde_json::Value;
use std::path::{Path, PathBuf};

fn timeline_from_events(con: &Connection, session_id: &str) -> Result<Vec<Event>, String> {
    let mut stmt = con
        .prepare("SELECT type, data FROM event WHERE aggregate_id = ?1 ORDER BY seq ASC, id ASC")
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([session_id], |row| {
            Ok((
                row.get::<_, String>(0).unwrap_or_default(),
                row.get::<_, String>(1).unwrap_or_default(),
            ))
        })
        .map_err(|e| e.to_string())?;
    let mut messages: Vec<(String, Value)> = Vec::new();
    let mut parts: std::collections::HashMap<String, Vec<Value>> = std::collections::HashMap::new();
    for row in rows.flatten() {
        let data: Value = serde_json::from_str(&row.1).unwrap_or(Value::Null);
        if row.0.starts_with("session.") {
            continue;
        }
        if row.0.contains("part") {
            if let Some(part) = data.get("part") {
                let mid = text::field_str(part, "messageID");
                if !mid.is_empty() {
                    parts.entry(mid).or_default().push(part.clone());
                }
            }
            continue;
        }
        if row.0.starts_with("message.") {
            if let Some(info) = data.get("info") {
                let mid = text::field_str(info, "id");
                if !mid.is_empty() {
                    messages.push((mid, info.clone()));
                }
            }
        }
    }
    let mut events = Vec::new();
    let mut turn = 0i32;
    for (mid, data) in messages {
        let role = text::field_str(&data, "role");
        let msg_parts = parts.get(&mid).cloned().unwrap_or_default();
        let raw = serde_json::to_string(&data).unwrap_or_default();
        if role == "user" {
            let mut start = Event::new(EventType::TurnStarted)
                .with_content(format!("turn_number={turn}"))
                .with_raw(&raw);
            start.turn_number = Some(turn);
            events.push(start);
            let mut text_body = String::new();
            for part in &msg_parts {
                if text::field_str(part, "type") == "text" {
                    text_body.push_str(&text::field_str(part, "text"));
                }
            }
            events.push(
                Event::new(EventType::UserMessageChunk)
                    .with_content(text_body)
                    .with_raw(raw),
            );
            turn += 1;
        } else {
            for part in msg_parts {
                let kind = text::field_str(&part, "type");
                let praw = serde_json::to_string(&part).unwrap_or_default();
                if kind == "text" {
                    events.push(
                        Event::new(EventType::AgentMessageChunk)
                            .with_content(text::field_str(&part, "text"))
                            .with_raw(praw),
                    );
                } else if kind == "tool" {
                    events.extend(tool_events(&part, &praw));
                }
            }
        }
    }
    Ok(events)
}

fn tool_events(part: &Value, raw: &str) -> Vec<Event> {
    let name = {
        let n = text::field_str(part, "tool");
        if n.is_empty() {
            "tool".into()
        } else {
            n
        }
    };
    let call_id = {
        let a = text::field_str(part, "callID");
        if a.is_empty() {
            text::field_str(part, "call_id")
        } else {
            a
        }
    };
    let state = part.get("state").cloned().unwrap_or(Value::Null);
    let inn = state.get("input").cloned().unwrap_or(Value::Null);
    let raw_in = serde_json::to_string(&inn).unwrap_or_else(|_| raw.to_string());
    let status = text::field_str(&state, "status").to_ascii_lowercase();
    let failed = matches!(status.as_str(), "error" | "failed") || state.get("error").is_some();
    let out = text::field_str(&state, "output");
    let mut call = Event::new(EventType::ToolCall)
        .with_content(&name)
        .with_raw(&raw_in);
    call.tool_name = name.clone();
    call.tool_call_id = call_id.clone();
    call.is_error = failed;
    let mut upd = Event::new(EventType::ToolCallUpdate)
        .with_content(&out)
        .with_raw(raw);
    upd.tool_name = name.clone();
    upd.tool_call_id = call_id;
    upd.is_error = failed;
    let mut events = vec![call, upd];
    if name == "task" {
        let meta = state.get("metadata").cloned().unwrap_or(Value::Null);
        let mut child = text::field_str(&meta, "sessionId");
        if child.is_empty() && !text::field_str(&inn, "subagent_type").is_empty() {
            if let Some(rest) = out.split_once("<task id=\"") {
                child = rest.1.split('"').next().unwrap_or("").to_string();
            }
        }
        if !child.is_empty() {
            let desc = text::field_str(&inn, "description");
            let typ = text::field_str(&inn, "subagent_type");
            let mut spawn = Event::new(EventType::SubagentSpawned)
                .with_content(&desc)
                .with_raw(&raw_in);
            spawn.child_session_id = child.clone();
            spawn.subagent_type = typ;
            spawn.description = desc;
            let mut fin = Event::new(EventType::SubagentFinished)
                .with_content(&out)
                .with_raw(raw);
            fin.child_session_id = child;
            fin.is_error = failed;
            events.push(spawn);
            events.push(fin);
        }
    }
    events
}

pub struct OpenCode;

fn open_ro(path: &Path) -> Result<Connection, String> {
    Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| e.to_string())
}

fn table_exists(con: &Connection, name: &str) -> bool {
    con.query_row(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1",
        [name],
        |_| Ok(()),
    )
    .is_ok()
}

impl Store for OpenCode {
    fn id(&self) -> &'static str {
        "opencode"
    }

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator> {
        let mut out = Vec::new();
        for raw in roots {
            let db = if raw.is_file() {
                raw.clone()
            } else {
                raw.join("opencode.db")
            };
            if !db.is_file() {
                continue;
            }
            let Ok(con) = open_ro(&db) else { continue };
            if table_exists(&con, "event") {
                if let Ok(mut stmt) =
                    con.prepare("SELECT data FROM event WHERE type LIKE 'session.created%'")
                {
                    if let Ok(rows) = stmt.query_map([], |row| row.get::<_, String>(0)) {
                        for raw in rows.flatten() {
                            let data: Value = serde_json::from_str(&raw).unwrap_or(Value::Null);
                            let info = data.get("info").cloned().unwrap_or(Value::Null);
                            let sid = text::field_str(&info, "id");
                            let parent = text::field_str(&info, "parentID");
                            if sid.is_empty() || !(parent.is_empty() || parent == "None") {
                                continue;
                            }
                            out.push(SessionLocator {
                                harness: "opencode".into(),
                                session_id: sid,
                                locator: db.clone(),
                                cwd: text::field_str(&info, "directory"),
                            });
                        }
                    }
                }
            }
            if table_exists(&con, "session") {
                if let Ok(mut stmt) = con.prepare(
                    "SELECT id, directory FROM session WHERE parent_id IS NULL OR parent_id = ''",
                ) {
                    if let Ok(rows) = stmt.query_map([], |row| {
                        Ok((
                            row.get::<_, String>(0)?,
                            row.get::<_, String>(1).unwrap_or_default(),
                        ))
                    }) {
                        for row in rows.flatten() {
                            out.push(SessionLocator {
                                harness: "opencode".into(),
                                session_id: row.0,
                                locator: db.clone(),
                                cwd: row.1,
                            });
                        }
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
        if !locator.is_file() {
            return Err(format!("opencode session not found: {session_id}"));
        }
        Ok(Vec::new())
    }

    fn events(&self, _records: &[crate::store::Record]) -> Vec<Event> {
        Vec::new()
    }

    fn timeline(&self, locator: &Path, session_id: &str) -> Result<Vec<Event>, String> {
        let con = open_ro(locator)?;
        let mut events = Vec::new();
        if table_exists(&con, "event") && !table_exists(&con, "message") {
            let mut events = timeline_from_events(&con, session_id)?;
            Event::carry_turn_numbers(&mut events);
            text::index_events(&mut events);
            return Ok(events);
        }
        if table_exists(&con, "message") {
            let mut stmt = con
                .prepare(
                    "SELECT id, data FROM message WHERE session_id = ?1 ORDER BY time_created, id",
                )
                .map_err(|e| e.to_string())?;
            let rows = stmt
                .query_map([session_id], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1).unwrap_or_default(),
                    ))
                })
                .map_err(|e| e.to_string())?;
            let mut parts: std::collections::HashMap<String, Vec<Value>> =
                std::collections::HashMap::new();
            if table_exists(&con, "part") {
                if let Ok(mut pstmt) =
                    con.prepare("SELECT message_id, data FROM part WHERE session_id = ?1 ORDER BY time_created, id")
                {
                    if let Ok(prows) = pstmt.query_map([session_id], |row| {
                        Ok((
                            row.get::<_, String>(0)?,
                            row.get::<_, String>(1).unwrap_or_default(),
                        ))
                    }) {
                        for prow in prows.flatten() {
                            let data: Value = serde_json::from_str(&prow.1).unwrap_or(Value::Null);
                            parts.entry(prow.0).or_default().push(data);
                        }
                    }
                }
            }
            let mut turn = 0i32;
            for row in rows.flatten() {
                let data: Value = serde_json::from_str(&row.1).unwrap_or(Value::Null);
                let role = text::field_str(&data, "role");
                let msg_parts = parts.get(&row.0).cloned().unwrap_or_default();
                if role == "user" {
                    let mut start = Event::new(EventType::TurnStarted)
                        .with_content(format!("turn_number={turn}"))
                        .with_raw(&row.1);
                    start.turn_number = Some(turn);
                    events.push(start);
                    let mut text_body = String::new();
                    for part in &msg_parts {
                        if text::field_str(part, "type") == "text" {
                            let t = text::field_str(part, "text");
                            if !t.is_empty() {
                                text_body.push_str(&t);
                            }
                        }
                    }
                    if text_body.is_empty() {
                        text_body = text::text_of(data.get("content").unwrap_or(&Value::Null));
                    }
                    events.push(
                        Event::new(EventType::UserMessageChunk)
                            .with_content(text_body)
                            .with_raw(row.1),
                    );
                    turn += 1;
                } else {
                    for part in msg_parts {
                        let kind = text::field_str(&part, "type");
                        let raw = serde_json::to_string(&part).unwrap_or_default();
                        if kind == "text" {
                            events.push(
                                Event::new(EventType::AgentMessageChunk)
                                    .with_content(text::field_str(&part, "text"))
                                    .with_raw(raw),
                            );
                        } else if kind == "reasoning" {
                            events.push(
                                Event::new(EventType::AgentThoughtChunk)
                                    .with_content(text::field_str(&part, "text"))
                                    .with_raw(raw),
                            );
                        } else if kind == "tool" {
                            events.extend(tool_events(&part, &raw));
                        }
                    }
                }
            }
        }
        Event::carry_turn_numbers(&mut events);
        text::index_events(&mut events);
        Ok(events)
    }
}
