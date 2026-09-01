//! Gemini CLI jsonl conversation.

use crate::event::{Event, EventType, ListMeta, SessionLocator};
use crate::jsonl;
use crate::store::Store;
use crate::text;
use serde_json::{Map, Value};
use std::path::{Path, PathBuf};

pub struct Gemini;

fn collect(roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for raw in roots {
        if raw.is_file()
            && raw
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("session-") && n.ends_with(".jsonl"))
        {
            out.push(raw.clone());
        } else if raw.is_dir() {
            out.extend(crate::walk::find_files(raw, ".jsonl", "session-"));
        }
    }
    out
}

fn load_conversation(path: &Path) -> (Value, Vec<Value>) {
    let mut metadata = Map::new();
    let mut messages: std::collections::HashMap<String, Value> = std::collections::HashMap::new();
    let mut order: Vec<String> = Vec::new();

    let put = |messages: &mut std::collections::HashMap<String, Value>,
               order: &mut Vec<String>,
               raw: &Value| {
        messages.clear();
        order.clear();
        let Some(items) = raw.as_array() else { return };
        for item in items {
            let id = text::field_str(item, "id");
            if id.is_empty() {
                continue;
            }
            if !messages.contains_key(&id) {
                order.push(id.clone());
            }
            messages.insert(id, item.clone());
        }
    };

    for row in jsonl::read_objects(path) {
        if row.value.get("$rewindTo").is_some() {
            messages.clear();
            order.clear();
            continue;
        }
        if let Some(patch) = row.value.get("$set").and_then(|v| v.as_object()) {
            if let Some(msgs) = patch.get("messages") {
                put(&mut messages, &mut order, msgs);
            }
            for (k, v) in patch {
                if k != "messages" {
                    metadata.insert(k.clone(), v.clone());
                }
            }
            continue;
        }
        let typ = text::field_str(&row.value, "type");
        let mid = text::field_str(&row.value, "id");
        if typ == "message_update" && !mid.is_empty() {
            if let Some(existing) = messages.get_mut(&mid) {
                if let (Some(a), Some(b)) = (existing.as_object_mut(), row.value.as_object()) {
                    let kept = text::field_str(&Value::Object(a.clone()), "type");
                    for (k, v) in b {
                        a.insert(k.clone(), v.clone());
                    }
                    if !kept.is_empty() {
                        a.insert("type".into(), Value::String(kept));
                    }
                }
            }
            continue;
        }
        if !mid.is_empty() && matches!(typ.as_str(), "user" | "gemini" | "error") {
            if !messages.contains_key(&mid) {
                order.push(mid.clone());
            }
            messages.insert(mid, row.value);
            continue;
        }
        if !text::field_str(&row.value, "sessionId").is_empty() {
            if let Some(obj) = row.value.as_object() {
                for (k, v) in obj {
                    if k == "messages" {
                        put(&mut messages, &mut order, v);
                    } else {
                        metadata.insert(k.clone(), v.clone());
                    }
                }
            }
        }
    }
    let list = order
        .into_iter()
        .filter_map(|id| messages.remove(&id))
        .collect();
    (Value::Object(metadata), list)
}

fn is_chrome(text: &str) -> bool {
    let t = text.trim_start();
    t.starts_with("<session_context>") || t.starts_with("<environment_context>")
}

fn timeline_of(messages: &[Value]) -> Vec<Event> {
    let mut events = Vec::new();
    let mut turn = 0i32;
    for msg in messages {
        let typ = text::field_str(msg, "type");
        let ts = text::field_i64(msg, "timestamp");
        let raw = serde_json::to_string(msg).unwrap_or_default();
        if typ == "user" {
            let body = text::text_of(msg.get("content").unwrap_or(&Value::Null));
            if body.trim().is_empty() || is_chrome(&body) {
                continue;
            }
            events.push(
                Event::new(EventType::TurnStarted)
                    .with_ts(ts)
                    .with_content(format!("turn_number={turn}"))
                    .with_raw(&raw),
            );
            events.push(
                Event::new(EventType::UserMessageChunk)
                    .with_ts(ts)
                    .with_content(body)
                    .with_raw(raw),
            );
            turn += 1;
            continue;
        }
        if typ == "error" {
            let mut ev = Event::new(EventType::SessionError)
                .with_ts(ts)
                .with_content(text::text_of(msg.get("content").unwrap_or(&Value::Null)))
                .with_raw(raw);
            ev.is_error = true;
            events.push(ev);
            continue;
        }
        if typ != "gemini" {
            continue;
        }
        if let Some(thoughts) = msg.get("thoughts").and_then(|v| v.as_array()) {
            for item in thoughts {
                let t = {
                    let d = text::field_str(item, "description");
                    if d.is_empty() {
                        text::text_of(item)
                    } else {
                        d
                    }
                };
                if !t.is_empty() {
                    events.push(
                        Event::new(EventType::AgentThoughtChunk)
                            .with_ts(ts)
                            .with_content(t)
                            .with_raw(&raw),
                    );
                }
            }
        }
        let body = text::text_of(msg.get("content").unwrap_or(&Value::Null));
        if !body.trim().is_empty() {
            events.push(
                Event::new(EventType::AgentMessageChunk)
                    .with_ts(ts)
                    .with_content(body)
                    .with_raw(&raw),
            );
        }
        if let Some(calls) = msg
            .get("toolCalls")
            .or_else(|| msg.get("tool_calls"))
            .and_then(|v| v.as_array())
        {
            for call in calls {
                let name = text::field_str(call, "name");
                let args = call
                    .get("args")
                    .or_else(|| call.get("arguments"))
                    .cloned()
                    .unwrap_or(Value::Null);
                let mut ev = Event::new(EventType::ToolCall)
                    .with_ts(ts)
                    .with_raw(serde_json::to_string(&args).unwrap_or_default());
                ev.tool_name = if name.is_empty() {
                    "tool".into()
                } else {
                    name.clone()
                };
                ev.tool_call_id = text::field_str(call, "id");
                if let Some(result) = call.get("result") {
                    let mut upd = Event::new(EventType::ToolCallUpdate)
                        .with_ts(ts)
                        .with_content(text::text_of(result))
                        .with_raw(&raw);
                    upd.tool_name = ev.tool_name.clone();
                    upd.tool_call_id = ev.tool_call_id.clone();
                    events.push(upd);
                }
                events.push(ev);
            }
        }
    }
    text::index_events(&mut events);
    events
}

impl Store for Gemini {
    fn id(&self) -> &'static str {
        "gemini"
    }

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator> {
        collect(roots)
            .into_iter()
            .filter_map(|file| {
                let first = jsonl::first_object(&file)?;
                let sid = text::field_str(&first.value, "sessionId");
                if sid.is_empty() {
                    return None;
                }
                Some(SessionLocator {
                    harness: "gemini".into(),
                    session_id: sid,
                    locator: file,
                    cwd: String::new(),
                })
            })
            .collect()
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_file() {
            return Err(format!("gemini session not found: {session_id}"));
        }
        Ok(ListMeta {
            session_id: session_id.to_string(),
            locator: locator.to_path_buf(),
            harness: "gemini".into(),
            model_id: "unknown".into(),
            ..ListMeta::default()
        })
    }

    fn timeline(&self, locator: &Path, session_id: &str) -> Result<Vec<Event>, String> {
        if !locator.is_file() {
            return Err(format!("gemini session not found: {session_id}"));
        }
        let (_meta, messages) = load_conversation(locator);
        Ok(timeline_of(&messages))
    }
}
