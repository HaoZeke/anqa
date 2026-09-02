//! Gemini CLI jsonl conversation.

use crate::event::{Event, EventType, SessionLocator};
use crate::jsonl;
use crate::store::Store;
use crate::text;
use serde_json::{Map, Value};
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex};

pub struct Gemini;

#[derive(Default)]
struct GeminiCursor {
    byte_pos: u64,
    metadata: Map<String, Value>,
    messages: HashMap<String, Value>,
    order: Vec<String>,
}

static CURSORS: LazyLock<Mutex<HashMap<PathBuf, GeminiCursor>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

impl GeminiCursor {
    fn load(path: &Path) -> (Value, Vec<Value>) {
        let key = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
        let mut guard = CURSORS.lock().unwrap_or_else(|e| e.into_inner());
        let cursor = guard.entry(key).or_default();
        cursor.sync(path);
        cursor.snapshot()
    }

    fn snapshot(&self) -> (Value, Vec<Value>) {
        let list = self
            .order
            .iter()
            .filter_map(|id| self.messages.get(id).cloned())
            .collect();
        (Value::Object(self.metadata.clone()), list)
    }

    fn replace_messages(&mut self, raw: &Value) {
        self.messages.clear();
        self.order.clear();
        let Some(items) = raw.as_array() else {
            return;
        };
        for item in items {
            let id = text::field_str(item, "id");
            if id.is_empty() {
                continue;
            }
            if !self.messages.contains_key(&id) {
                self.order.push(id.clone());
            }
            self.messages.insert(id, item.clone());
        }
    }

    fn apply(&mut self, row: &Value) {
        if row.get("$rewindTo").is_some() {
            self.messages.clear();
            self.order.clear();
            return;
        }
        if let Some(patch) = row.get("$set").and_then(|v| v.as_object()) {
            if let Some(msgs) = patch.get("messages") {
                self.replace_messages(msgs);
            }
            for (k, v) in patch {
                if k != "messages" {
                    self.metadata.insert(k.clone(), v.clone());
                }
            }
            return;
        }
        let typ = text::field_str(row, "type");
        let mid = text::field_str(row, "id");
        if typ == "message_update" && !mid.is_empty() {
            if let Some(existing) = self.messages.get_mut(&mid) {
                if let (Some(a), Some(b)) = (existing.as_object_mut(), row.as_object()) {
                    let kept = text::field_str(&Value::Object(a.clone()), "type");
                    for (k, v) in b {
                        a.insert(k.clone(), v.clone());
                    }
                    if !kept.is_empty() {
                        a.insert("type".into(), Value::String(kept));
                    }
                }
            }
            return;
        }
        if !mid.is_empty() && matches!(typ.as_str(), "user" | "gemini" | "error") {
            if !self.messages.contains_key(&mid) {
                self.order.push(mid.clone());
            }
            self.messages.insert(mid, row.clone());
            return;
        }
        if !text::field_str(row, "sessionId").is_empty() {
            if let Some(obj) = row.as_object() {
                for (k, v) in obj {
                    if k == "messages" {
                        self.replace_messages(v);
                    } else {
                        self.metadata.insert(k.clone(), v.clone());
                    }
                }
            }
        }
    }

    fn sync(&mut self, path: &Path) {
        let Ok(mut file) = File::open(path) else {
            return;
        };
        let size = file.metadata().map(|m| m.len()).unwrap_or(0);
        if size < self.byte_pos {
            *self = Self::default();
        }
        if file.seek(SeekFrom::Start(self.byte_pos)).is_err() {
            return;
        }
        let mut reader = BufReader::new(file);
        loop {
            let mut line = String::new();
            let n = match reader.read_line(&mut line) {
                Ok(0) | Err(_) => break,
                Ok(n) => n,
            };
            if !line.ends_with('\n') {
                break;
            }
            self.byte_pos += n as u64;
            if let Some(value) = jsonl::object_line(&line) {
                self.apply(&value);
            }
        }
    }
}

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
            let mut start = Event::new(EventType::TurnStarted)
                .with_ts(ts)
                .with_content(format!("turn_number={turn}"))
                .with_raw(&raw);
            start.turn_number = Some(turn);
            events.push(start);
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

    fn records(
        &self,
        locator: &Path,
        session_id: &str,
    ) -> Result<Vec<crate::store::Record>, String> {
        if !locator.is_file() {
            return Err(format!("gemini session not found: {session_id}"));
        }
        let (_meta, messages) = GeminiCursor::load(locator);
        Ok(messages
            .into_iter()
            .map(|value| crate::store::Record {
                raw: serde_json::to_string(&value).unwrap_or_default(),
                value,
            })
            .collect())
    }

    fn events(&self, records: &[crate::store::Record]) -> Vec<Event> {
        let messages: Vec<Value> = records.iter().map(|r| r.value.clone()).collect();
        timeline_of(&messages)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::EventType;
    use std::fs;
    use std::io::Write;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn user_texts(events: &[Event]) -> Vec<&str> {
        events
            .iter()
            .filter(|ev| ev.event_type == EventType::UserMessageChunk)
            .map(|ev| ev.content.as_str())
            .collect()
    }

    #[test]
    fn gemini_append_message_updates_conversation() {
        let root = std::env::temp_dir().join(format!(
            "anqa-gemini-cursor-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let path = root.join("session-2026-08-09T12-00-cursor.jsonl");
        let sid = "sess-gemini-cursor";
        let mut file = fs::File::create(&path).unwrap();
        writeln!(
            file,
            r#"{{"sessionId":"{sid}","projectHash":"abc","kind":"main"}}"#
        )
        .unwrap();
        writeln!(
            file,
            r#"{{"id":"u1","type":"user","content":[{{"text":"hello"}}],"timestamp":1}}"#
        )
        .unwrap();
        writeln!(
            file,
            r#"{{"id":"g1","type":"gemini","content":[{{"text":"ok"}}],"timestamp":2}}"#
        )
        .unwrap();
        file.flush().unwrap();
        drop(file);

        let first = crate::timeline("gemini", &path, sid).unwrap();
        assert_eq!(user_texts(&first), ["hello"]);

        let mut file = fs::OpenOptions::new().append(true).open(&path).unwrap();
        writeln!(
            file,
            r#"{{"id":"u2","type":"user","content":[{{"text":"again"}}],"timestamp":3}}"#
        )
        .unwrap();
        file.flush().unwrap();
        drop(file);

        let appended = crate::timeline("gemini", &path, sid).unwrap();
        assert_eq!(user_texts(&appended), ["hello", "again"]);

        let mut file = fs::OpenOptions::new().append(true).open(&path).unwrap();
        writeln!(file, r#"{{"$rewindTo":""}}"#).unwrap();
        file.flush().unwrap();
        drop(file);

        let rewound = crate::timeline("gemini", &path, sid).unwrap();
        assert!(
            user_texts(&rewound).is_empty(),
            "rewind must clear conversation, not concat records: {:?}",
            user_texts(&rewound)
        );

        let _ = fs::remove_dir_all(&root);
    }
}
