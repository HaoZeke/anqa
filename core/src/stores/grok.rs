//! Grok session directory (`updates.jsonl` + `events.jsonl`).

use crate::event::{Event, EventType, SessionLocator};
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

fn update_raw(update: &Value, line: &str) -> String {
    serde_json::to_string(update).unwrap_or_else(|_| line.to_string())
}

fn prompt_index(update: &Value) -> Option<i32> {
    update
        .get("_meta")
        .and_then(|meta| text::field_i64(meta, "promptIndex"))
        .map(|n| n as i32)
}

fn task_family_raw(update: &Value, line: &str) -> String {
    let mut merged = serde_json::Map::new();
    if let Some(Value::Object(snap)) = update.get("task_snapshot") {
        for (key, val) in snap {
            merged.insert(key.clone(), val.clone());
        }
    }
    if let Some(obj) = update.as_object() {
        for (key, val) in obj {
            merged.insert(key.clone(), val.clone());
        }
    }
    serde_json::to_string(&Value::Object(merged)).unwrap_or_else(|_| line.to_string())
}

fn consume_line(
    line: &str,
    events: &mut Vec<Event>,
    results: &mut std::collections::HashMap<String, usize>,
) {
    if !keep_updates_line(line.as_bytes()) {
        return;
    }
    let Some(val) = jsonl::object_line(line) else {
        return;
    };
    let update = val
        .pointer("/params/update")
        .cloned()
        .unwrap_or(Value::Null);
    let kind = EventType::parse(&text::field_str(&update, "sessionUpdate"));
    let ts = text::epoch(&val);
    match kind {
        EventType::UserMessageChunk
        | EventType::AgentMessageChunk
        | EventType::AgentThoughtChunk => {
            let mapped = kind;
            let content = text::text_of(update.get("content").unwrap_or(&Value::Null));
            if let Some(prev) = events.last_mut() {
                if prev.event_type == mapped {
                    if mapped != EventType::UserMessageChunk {
                        prev.content.push_str(&content);
                        prev.timestamp = ts.or(prev.timestamp);
                        if prev.prompt_index.is_none() {
                            prev.prompt_index = prompt_index(&update);
                        }
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
                        if prev.prompt_index.is_none() {
                            prev.prompt_index = prompt_index(&update);
                        }
                        return;
                    }
                }
            }
            let mut ev = Event::new(mapped)
                .with_ts(ts)
                .with_content(content)
                .with_raw(update_raw(&update, line));
            ev.prompt_index = prompt_index(&update);
            events.push(ev);
        }
        EventType::ToolCall => {
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
        EventType::ToolCallUpdate => {
            let call_id = text::field_str(&update, "toolCallId");
            let body = text::text_of(update.get("content").unwrap_or(&Value::Null));
            let failed = update.get("isError") == Some(&Value::Bool(true))
                || text::field_str(&update, "status") == "failed";
            let terminal = failed
                || matches!(
                    text::field_str(&update, "status").as_str(),
                    "completed" | "failed"
                );
            if body.is_empty() && !failed {
                return;
            }
            if let Some(&idx) = results.get(&call_id) {
                if let Some(ev) = events.get_mut(idx) {
                    if !body.is_empty() && (body.len() >= ev.content.len() || terminal) {
                        ev.content = body;
                    }
                    ev.timestamp = ts.or(ev.timestamp);
                    if failed {
                        ev.is_error = true;
                    }
                }
                return;
            }
            let mut ev = Event::new(EventType::ToolCallUpdate)
                .with_ts(ts)
                .with_content(body)
                .with_raw(update_raw(&update, line));
            ev.tool_name = text::field_str(&update, "toolName");
            ev.tool_call_id = call_id.clone();
            ev.is_error = failed;
            results.insert(call_id, events.len());
            events.push(ev);
        }
        EventType::SubagentSpawned => {
            let mut ev = Event::new(EventType::SubagentSpawned)
                .with_ts(ts)
                .with_content(text::field_str(&update, "description"))
                .with_raw(update_raw(&update, line));
            ev.child_session_id = text::field_str(&update, "subagentId");
            if ev.child_session_id.is_empty() {
                ev.child_session_id = text::field_str(&update, "childSessionId");
            }
            ev.subagent_type = text::field_str(&update, "subagentType");
            ev.description = text::field_str(&update, "description");
            events.push(ev);
        }
        EventType::SubagentFinished => {
            let mut ev = Event::new(EventType::SubagentFinished)
                .with_ts(ts)
                .with_content(text::field_str(&update, "description"))
                .with_raw(update_raw(&update, line));
            ev.child_session_id = text::field_str(&update, "subagentId");
            events.push(ev);
        }
        EventType::TaskBackgrounded => {
            events.push(
                Event::new(EventType::TaskBackgrounded)
                    .with_ts(ts)
                    .with_raw(task_family_raw(&update, line)),
            );
        }
        EventType::TaskCompleted => {
            events.push(
                Event::new(EventType::TaskCompleted)
                    .with_ts(ts)
                    .with_raw(task_family_raw(&update, line)),
            );
        }
        EventType::TurnCompleted => {
            events.push(
                Event::new(EventType::TurnCompleted)
                    .with_ts(ts)
                    .with_raw(update_raw(&update, line)),
            );
        }
        EventType::Other(_) => {}
        other => {
            let raw = if other.is_scheduled_task() {
                task_family_raw(&update, line)
            } else {
                update_raw(&update, line)
            };
            events.push(
                Event::new(other)
                    .with_ts(ts)
                    .with_content(text::text_of(update.get("content").unwrap_or(&Value::Null)))
                    .with_raw(raw),
            );
        }
    }
}

fn map_events_row(row: &crate::store::Record, events: &mut Vec<Event>) {
    let kind = EventType::parse(&text::field_str(&row.value, "type"));
    let ts = text::epoch(&row.value);
    match &kind {
        EventType::TurnStarted => {
            let tn = text::field_i64(&row.value, "turn_number").map(|n| n as i32);
            let mut parts = vec!["turn started".to_string()];
            if let Some(n) = tn {
                parts.push(format!("turn_number={n}"));
            }
            let model = text::field_str(&row.value, "model_id");
            if !model.is_empty() {
                parts.push(format!("model={model}"));
            }
            let mut ev = Event::new(EventType::TurnStarted)
                .with_ts(ts)
                .with_content(parts.join("  "))
                .with_raw(&row.raw);
            ev.turn_number = tn;
            events.push(ev);
        }
        EventType::TurnEnded => {
            let outcome = text::field_str(&row.value, "outcome");
            let mut ev = Event::new(EventType::TurnEnded)
                .with_ts(ts)
                .with_content(format!("turn ended  outcome={outcome}"))
                .with_raw(&row.raw);
            if !matches!(
                outcome.to_ascii_lowercase().as_str(),
                "" | "success" | "ok" | "completed" | "complete" | "interjected"
            ) {
                ev.is_error = true;
            }
            events.push(ev);
        }
        EventType::SessionError
        | EventType::Error
        | EventType::TurnError
        | EventType::FatalError => {
            let label = kind.as_str();
            let msg = ["message", "error", "detail"]
                .iter()
                .map(|k| text::field_str(&row.value, k))
                .find(|s| !s.is_empty())
                .unwrap_or_else(|| label.to_string());
            let mut ev = Event::new(EventType::SessionError)
                .with_ts(ts)
                .with_content(format!("{label}: {msg}"))
                .with_raw(&row.raw);
            ev.is_error = true;
            events.push(ev);
        }
        _ => {}
    }
}

fn read_updates(dir: &Path) -> Vec<crate::store::Record> {
    let path = dir.join("updates.jsonl");
    let Ok(file) = File::open(&path) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for line in BufReader::new(file).lines().map_while(Result::ok) {
        if !keep_updates_line(line.as_bytes()) {
            continue;
        }
        if let Some(value) = jsonl::object_line(&line) {
            out.push(crate::store::Record { raw: line, value });
        }
    }
    out
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

    fn records(
        &self,
        locator: &Path,
        session_id: &str,
    ) -> Result<Vec<crate::store::Record>, String> {
        if !locator.is_dir() {
            return Err(format!("grok session not found: {session_id}"));
        }
        let mut rows = read_updates(locator);
        rows.extend(jsonl::read_objects(&locator.join("events.jsonl")));
        Ok(rows)
    }

    fn events(&self, records: &[crate::store::Record]) -> Vec<Event> {
        let mut events = Vec::new();
        let mut results = std::collections::HashMap::new();
        for rec in records {
            if rec.value.pointer("/params/update").is_some() {
                consume_line(&rec.raw, &mut events, &mut results);
            } else {
                map_events_row(rec, &mut events);
            }
        }
        events.sort_by_key(|ev| {
            let ts = ev.timestamp.unwrap_or(i64::MAX);
            let start = u8::from(ev.event_type == EventType::TurnStarted);
            (ts, ev.update_index, start, ev.index)
        });
        events
    }

    fn stamp(&self, locator: &Path) -> crate::event::FileStamp {
        jsonl::file_stamp(&locator.join("updates.jsonl"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io::Write;

    #[test]
    fn turn_markers_interleave_and_carry_turn_number() {
        let dir = std::env::temp_dir().join(format!("anqa-grok-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let mut u = fs::File::create(dir.join("updates.jsonl")).unwrap();
        writeln!(
            u,
            r#"{{"timestamp":100,"params":{{"update":{{"sessionUpdate":"user_message_chunk","content":"hi"}}}}}}"#
        )
        .unwrap();
        writeln!(
            u,
            r#"{{"timestamp":200,"params":{{"update":{{"sessionUpdate":"agent_message_chunk","content":"yo"}}}}}}"#
        )
        .unwrap();
        drop(u);
        let mut e = fs::File::create(dir.join("events.jsonl")).unwrap();
        writeln!(
            e,
            r#"{{"ts":"1970-01-01T00:01:30Z","type":"turn_started","turn_number":0}}"#
        )
        .unwrap();
        writeln!(
            e,
            r#"{{"ts":"1970-01-01T00:03:30Z","type":"turn_ended","outcome":"completed"}}"#
        )
        .unwrap();
        drop(e);
        let evs = Grok.timeline(&dir, "sess").unwrap();
        let types: Vec<_> = evs.iter().map(|e| e.event_type.as_str()).collect();
        assert_eq!(
            types,
            [
                "turn_started",
                "user_message_chunk",
                "agent_message_chunk",
                "turn_ended"
            ]
        );
        assert_eq!(evs[0].turn_number, Some(0));
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn runtime_noise_is_not_a_timeline_row() {
        let dir = std::env::temp_dir().join(format!("anqa-grok-noise-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let mut u = fs::File::create(dir.join("updates.jsonl")).unwrap();
        writeln!(
            u,
            r#"{{"timestamp":100,"params":{{"update":{{"sessionUpdate":"user_message_chunk","content":"hi"}}}}}}"#
        )
        .unwrap();
        writeln!(
            u,
            r#"{{"timestamp":101,"params":{{"update":{{"sessionUpdate":"phase_changed","phase":"act"}}}}}}"#
        )
        .unwrap();
        drop(u);
        let mut e = fs::File::create(dir.join("events.jsonl")).unwrap();
        for typ in [
            r#"{"ts":"1970-01-01T00:01:30Z","type":"turn_started","turn_number":0}"#,
            r#"{"ts":"1970-01-01T00:01:31Z","type":"loop_started","loop_index":0}"#,
            r#"{"ts":"1970-01-01T00:01:32Z","type":"first_token"}"#,
            r#"{"ts":"1970-01-01T00:01:33Z","type":"phase_changed","phase":"act"}"#,
            r#"{"ts":"1970-01-01T00:01:34Z","type":"tool_started"}"#,
            r#"{"ts":"1970-01-01T00:01:35Z","type":"permission_requested"}"#,
            r#"{"ts":"1970-01-01T00:01:36Z","type":"permission_resolved"}"#,
            r#"{"ts":"1970-01-01T00:01:37Z","type":"tool_completed"}"#,
            r#"{"ts":"1970-01-01T00:03:30Z","type":"turn_ended","outcome":"completed"}"#,
        ] {
            writeln!(e, "{typ}").unwrap();
        }
        drop(e);
        let evs = Grok.timeline(&dir, "sess").unwrap();
        let types: Vec<_> = evs.iter().map(|e| e.event_type.as_str()).collect();
        assert_eq!(types, ["turn_started", "user_message_chunk", "turn_ended"]);
        fs::remove_dir_all(&dir).unwrap();
    }
}
