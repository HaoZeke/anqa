//! Grok session directory (`updates.jsonl` + `events.jsonl`).

use crate::event::{Event, EventType, ListMeta, SessionLocator};
use crate::jsonl;
use crate::scan::keep_updates_line;
use crate::store::Store;
use crate::text;
use crate::walk;
use serde_json::Value;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};

pub struct Grok;

fn consume_line(line: &str, events: &mut Vec<Event>) {
    if !keep_updates_line(line.as_bytes()) {
        return;
    }
    let Some(val) = jsonl::object_line(line) else {
        return;
    };
    let params = val.get("params").cloned().unwrap_or(Value::Null);
    let update = params.get("update").cloned().unwrap_or(Value::Null);
    let etype = text::field_str(&update, "sessionUpdate");
    let ts = text::field_i64(&val, "timestamp").or_else(|| text::field_i64(&val, "ts"));
    match etype.as_str() {
        "user_message_chunk" | "agent_message_chunk" | "agent_thought_chunk" => {
            let mapped = EventType::parse(&etype);
            let content = text::text_of(update.get("content").unwrap_or(&Value::Null));
            if let Some(prev) = events.last_mut() {
                if prev.event_type == mapped {
                    if etype != "user_message_chunk" {
                        prev.content.push_str(&content);
                        prev.timestamp = ts.or(prev.timestamp);
                        return;
                    }
                    let old = prev.content.clone();
                    if content.is_empty() {
                        prev.timestamp = ts.or(prev.timestamp);
                        return;
                    }
                    if old.is_empty() || content.starts_with(&old) || old.starts_with(&content) {
                        if content.len() >= old.len() {
                            prev.content = content;
                        }
                        prev.timestamp = ts.or(prev.timestamp);
                        return;
                    }
                }
            }
            events.push(
                Event::new(mapped)
                    .with_ts(ts)
                    .with_content(content)
                    .with_raw(line),
            );
        }
        "tool_call" => {
            let name = {
                let n = text::field_str(&update, "toolName");
                if n.is_empty() {
                    text::field_str(&update, "title")
                } else {
                    n
                }
            };
            let call_id = text::field_str(&update, "toolCallId");
            let args = update
                .get("input")
                .or_else(|| update.get("rawInput"))
                .cloned()
                .unwrap_or(Value::Null);
            let mut ev = Event::new(EventType::ToolCall)
                .with_ts(ts)
                .with_content(name.clone())
                .with_raw(serde_json::to_string(&args).unwrap_or_else(|_| line.to_string()));
            ev.tool_name = if name.is_empty() { "tool".into() } else { name };
            ev.tool_call_id = call_id;
            events.push(ev);
        }
        "tool_call_update" => {
            let mut ev = Event::new(EventType::ToolCallUpdate)
                .with_ts(ts)
                .with_content(text::text_of(update.get("content").unwrap_or(&Value::Null)))
                .with_raw(line);
            ev.tool_name = text::field_str(&update, "toolName");
            ev.tool_call_id = text::field_str(&update, "toolCallId");
            ev.is_error = update.get("isError") == Some(&Value::Bool(true))
                || text::field_str(&update, "status") == "failed";
            events.push(ev);
        }
        "subagent_spawned" | "subagent_started" => {
            let mut ev = Event::new(EventType::SubagentSpawned)
                .with_ts(ts)
                .with_content(text::field_str(&update, "description"))
                .with_raw(line);
            ev.child_session_id = text::field_str(&update, "subagentId");
            if ev.child_session_id.is_empty() {
                ev.child_session_id = text::field_str(&update, "childSessionId");
            }
            ev.subagent_type = text::field_str(&update, "subagentType");
            ev.description = text::field_str(&update, "description");
            events.push(ev);
        }
        "subagent_finished" | "subagent_completed" => {
            let mut ev = Event::new(EventType::SubagentFinished)
                .with_ts(ts)
                .with_content(text::field_str(&update, "description"))
                .with_raw(line);
            ev.child_session_id = text::field_str(&update, "subagentId");
            events.push(ev);
        }
        "task_backgrounded" => {
            events.push(
                Event::new(EventType::TaskBackgrounded)
                    .with_ts(ts)
                    .with_raw(line),
            );
        }
        "task_completed" => {
            events.push(
                Event::new(EventType::TaskCompleted)
                    .with_ts(ts)
                    .with_raw(line),
            );
        }
        "turn_completed" => {
            events.push(
                Event::new(EventType::TurnCompleted)
                    .with_ts(ts)
                    .with_raw(line),
            );
        }
        other if !other.is_empty() => {
            events.push(
                Event::new(EventType::parse(other))
                    .with_ts(ts)
                    .with_content(text::text_of(update.get("content").unwrap_or(&Value::Null)))
                    .with_raw(line),
            );
        }
        _ => {}
    }
}

fn events_jsonl(dir: &Path, events: &mut Vec<Event>) {
    let path = dir.join("events.jsonl");
    if !path.is_file() {
        return;
    }
    for row in jsonl::read_objects(&path) {
        let typ = text::field_str(&row.value, "type");
        if typ.is_empty() {
            continue;
        }
        let ts = text::field_i64(&row.value, "timestamp");
        let ev_type = EventType::parse(&typ);
        events.push(
            Event::new(ev_type)
                .with_ts(ts)
                .with_content(text::text_of(
                    row.value.get("content").unwrap_or(&Value::Null),
                ))
                .with_raw(row.raw),
        );
    }
}

fn parse_dir(dir: &Path) -> Vec<Event> {
    let mut events = Vec::new();
    let updates = dir.join("updates.jsonl");
    if let Ok(file) = File::open(&updates) {
        for line in BufReader::new(file).lines().map_while(Result::ok) {
            consume_line(&line, &mut events);
        }
    }
    events_jsonl(dir, &mut events);
    text::index_events(&mut events);
    events
}

impl Store for Grok {
    fn id(&self) -> &'static str {
        "grok"
    }

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator> {
        let mut out = Vec::new();
        for raw in roots {
            if walk::looks_like_session_dir(raw) {
                out.push(SessionLocator {
                    harness: "grok".into(),
                    session_id: raw
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or("")
                        .into(),
                    locator: raw.clone(),
                    cwd: String::new(),
                });
                continue;
            }
            if raw.is_dir() {
                for dir in walk::find_sessions(raw) {
                    out.push(SessionLocator {
                        harness: "grok".into(),
                        session_id: dir
                            .file_name()
                            .and_then(|n| n.to_str())
                            .unwrap_or("")
                            .into(),
                        locator: dir,
                        cwd: String::new(),
                    });
                }
            }
        }
        out
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_dir() {
            return Err(format!("grok session not found: {session_id}"));
        }
        Ok(ListMeta {
            session_id: session_id.to_string(),
            locator: locator.to_path_buf(),
            harness: "grok".into(),
            model_id: "unknown".into(),
            ..ListMeta::default()
        })
    }

    fn timeline(&self, locator: &Path, session_id: &str) -> Result<Vec<Event>, String> {
        if !locator.is_dir() {
            return Err(format!("grok session not found: {session_id}"));
        }
        Ok(parse_dir(locator))
    }

    fn stamp(&self, locator: &Path) -> crate::event::FileStamp {
        jsonl::file_stamp(&locator.join("updates.jsonl"))
    }
}
