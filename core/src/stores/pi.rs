//! Pi jsonl store.

use crate::event::{Event, EventType, ListMeta, ListStatus, SessionLocator};
use crate::jsonl::{self, JsonlRow};
use crate::store::Store;
use crate::text;
use serde_json::Value;
use std::path::{Path, PathBuf};

pub struct Pi;

fn session_id_from_name(path: &Path) -> String {
    let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
    stem.rsplit_once('_')
        .map(|(_, id)| id.to_string())
        .unwrap_or_else(|| stem.to_string())
}

fn header_id(path: &Path) -> Option<String> {
    let row = jsonl::first_object(path)?;
    if text::field_str(&row.value, "type") == "session" {
        let id = text::field_str(&row.value, "id");
        if !id.is_empty() {
            return Some(id);
        }
    }
    None
}

fn collect(roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for raw in roots {
        let files = if raw.is_file() && raw.extension().and_then(|s| s.to_str()) == Some("jsonl") {
            vec![raw.clone()]
        } else if raw.is_dir() {
            crate::walk::find_files(raw, ".jsonl", "")
        } else {
            continue;
        };
        for file in files {
            let key = file.canonicalize().unwrap_or_else(|_| file.clone());
            if seen.insert(key) {
                out.push(file);
            }
        }
    }
    out
}

fn timeline_rows(rows: &[JsonlRow]) -> Vec<Event> {
    let mut events = Vec::new();
    let mut turn = 0i32;
    for row in rows {
        if text::field_str(&row.value, "type") != "message" {
            continue;
        }
        let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
        let role = text::field_str(&msg, "role");
        let ts =
            text::field_i64(&row.value, "timestamp").or_else(|| text::field_i64(&msg, "timestamp"));
        if role == "user" {
            let mut start = Event::new(EventType::TurnStarted)
                .with_ts(ts)
                .with_content(format!("turn_number={turn}"))
                .with_raw(row.raw.clone());
            start.turn_number = Some(turn);
            events.push(start);
            events.push(
                Event::new(EventType::UserMessageChunk)
                    .with_ts(ts)
                    .with_content(text::text_of(msg.get("content").unwrap_or(&Value::Null)))
                    .with_raw(row.raw.clone()),
            );
            turn += 1;
            continue;
        }
        if role == "toolResult" {
            events.extend(tool_result_events(&msg, ts, &row.raw));
            continue;
        }
        if role == "assistant" {
            events.extend(assistant_events(&msg, ts, &row.raw));
        }
    }
    events
}

fn assistant_events(msg: &Value, ts: Option<i64>, raw: &str) -> Vec<Event> {
    let mut out = Vec::new();
    let Some(blocks) = msg.get("content").and_then(|v| v.as_array()) else {
        return out;
    };
    for block in blocks {
        let kind = text::field_str(block, "type");
        if kind == "thinking" {
            let content = block
                .get("thinking")
                .or_else(|| block.get("text"))
                .map(text::as_str)
                .unwrap_or_default();
            out.push(
                Event::new(EventType::AgentThoughtChunk)
                    .with_ts(ts)
                    .with_content(content)
                    .with_raw(raw),
            );
        } else if kind == "text" {
            out.push(
                Event::new(EventType::AgentMessageChunk)
                    .with_ts(ts)
                    .with_content(text::field_str(block, "text"))
                    .with_raw(raw),
            );
        } else if kind == "toolCall" {
            let name = {
                let n = text::field_str(block, "name");
                if n.is_empty() {
                    "tool".into()
                } else {
                    n
                }
            };
            let call_id = text::field_str(block, "id");
            let args = block.get("arguments").cloned().unwrap_or(Value::Null);
            let raw_args = serde_json::to_string(&args).unwrap_or_default();
            let mut ev = Event::new(EventType::ToolCall)
                .with_ts(ts)
                .with_content(name.clone())
                .with_raw(raw_args);
            ev.tool_name = name.clone();
            ev.tool_call_id = call_id.clone();
            out.push(ev);
            if name == "subagent" {
                if let Some(tasks) = args.get("tasks").and_then(|v| v.as_array()) {
                    for (i, task) in tasks.iter().enumerate() {
                        let agent = {
                            let a = text::field_str(task, "agent");
                            if a.is_empty() {
                                "worker".into()
                            } else {
                                a
                            }
                        };
                        let desc = text::field_str(task, "task");
                        let mut spawn = Event::new(EventType::SubagentSpawned)
                            .with_ts(ts)
                            .with_content(format!("spawned {agent}: {desc}").trim())
                            .with_raw(raw);
                        spawn.child_session_id = format!("{call_id}:{i}");
                        spawn.subagent_type = agent;
                        spawn.description = desc.chars().take(320).collect();
                        out.push(spawn);
                    }
                }
            }
        }
    }
    out
}

fn tool_result_events(msg: &Value, ts: Option<i64>, raw: &str) -> Vec<Event> {
    let name = {
        let n = text::field_str(msg, "toolName");
        if n.is_empty() {
            "tool".into()
        } else {
            n
        }
    };
    if name == "subagent" {
        return subagent_finish_events(msg, ts, raw);
    }
    let mut ev = Event::new(EventType::ToolCallUpdate)
        .with_ts(ts)
        .with_content(text::text_of(msg.get("content").unwrap_or(&Value::Null)))
        .with_raw(raw);
    ev.tool_name = name;
    ev.tool_call_id = text::field_str(msg, "toolCallId");
    if text::field_str(msg, "isError") == "true" || msg.get("isError") == Some(&Value::Bool(true)) {
        ev.is_error = true;
    }
    vec![ev]
}

fn subagent_finish_events(msg: &Value, ts: Option<i64>, raw: &str) -> Vec<Event> {
    let call_id = text::field_str(msg, "toolCallId");
    let details = msg.get("details").cloned().unwrap_or(Value::Null);
    let results = details.get("results").and_then(|v| v.as_array());
    if results.is_none() || results.is_some_and(|r| r.is_empty()) {
        let mut ev = Event::new(EventType::SubagentFinished)
            .with_ts(ts)
            .with_content(
                text::text_of(msg.get("content").unwrap_or(&Value::Null))
                    .chars()
                    .take(400)
                    .collect::<String>(),
            )
            .with_raw(raw);
        ev.tool_name = "subagent".into();
        ev.tool_call_id = call_id.clone();
        ev.child_session_id = format!("{call_id}:0");
        ev.subagent_type = "worker".into();
        ev.is_error = msg.get("isError") == Some(&Value::Bool(true));
        return vec![ev];
    }
    let mut out = Vec::new();
    for (i, item) in results.unwrap().iter().enumerate() {
        let agent = {
            let a = text::field_str(item, "agent");
            if a.is_empty() {
                "worker".into()
            } else {
                a
            }
        };
        let text_body = last_assistant_text(item.get("messages"))
            .unwrap_or_else(|| text::text_of(item.get("task").unwrap_or(&Value::Null)));
        let mut ev = Event::new(EventType::SubagentFinished)
            .with_ts(ts)
            .with_content(text_body.chars().take(400).collect::<String>())
            .with_raw(raw);
        ev.tool_name = "subagent".into();
        ev.tool_call_id = call_id.clone();
        ev.child_session_id = format!("{call_id}:{i}");
        ev.subagent_type = agent;
        out.push(ev);
    }
    out
}

fn last_assistant_text(messages: Option<&Value>) -> Option<String> {
    let items = messages?.as_array()?;
    let mut text = None;
    for item in items {
        if text::field_str(item, "role") != "assistant" {
            continue;
        }
        let body = text::text_of(item.get("content").unwrap_or(&Value::Null));
        if !body.trim().is_empty() {
            text = Some(body);
        }
    }
    text
}

fn last_role_outcome(rows: &[JsonlRow]) -> String {
    for row in rows.iter().rev() {
        if text::field_str(&row.value, "type") != "message" {
            continue;
        }
        let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
        let role = text::field_str(&msg, "role");
        if role == "user" {
            return ListStatus::Idle.as_str().into();
        }
        if role == "toolResult" {
            return ListStatus::Running.as_str().into();
        }
        if role == "assistant" {
            let reason = text::field_str(&msg, "stopReason");
            let compact = reason.to_ascii_lowercase().replace('_', "");
            if compact == "tooluse" {
                return ListStatus::Running.as_str().into();
            }
            return ListStatus::from_token(&reason).as_str().into();
        }
    }
    String::new()
}

fn model_from_rows(rows: &[JsonlRow]) -> String {
    let mut provider = String::new();
    let mut model = String::new();
    for row in rows {
        if text::field_str(&row.value, "type") != "model_change" {
            continue;
        }
        let p = text::field_str(&row.value, "provider");
        if !p.is_empty() {
            provider = p;
        }
        let m = text::field_str(&row.value, "modelId");
        if !m.is_empty() {
            model = m;
        }
    }
    match (provider.is_empty(), model.is_empty()) {
        (false, false) => format!("{provider}/{model}"),
        (true, false) => model,
        (false, true) => provider,
        (true, true) => "unknown".into(),
    }
}

fn first_user_title(rows: &[JsonlRow]) -> String {
    for row in rows {
        if text::field_str(&row.value, "type") != "message" {
            continue;
        }
        let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
        if text::field_str(&msg, "role") != "user" {
            continue;
        }
        let title = text::first_line(
            &text::text_of(msg.get("content").unwrap_or(&Value::Null)),
            80,
        );
        if !title.is_empty() {
            return title;
        }
    }
    String::new()
}

fn count_tools(rows: &[JsonlRow]) -> (u32, u32) {
    let mut tools = 0u32;
    let mut kids = 0u32;
    for row in rows {
        if text::field_str(&row.value, "type") != "message" {
            continue;
        }
        let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
        let Some(blocks) = msg.get("content").and_then(|v| v.as_array()) else {
            continue;
        };
        for block in blocks {
            if text::field_str(block, "type") != "toolCall" {
                continue;
            }
            tools += 1;
            if text::field_str(block, "name") == "subagent" {
                if let Some(tasks) = block.pointer("/arguments/tasks").and_then(|v| v.as_array()) {
                    kids += tasks.len() as u32;
                }
            }
        }
    }
    (tools, kids)
}

impl Store for Pi {
    fn id(&self) -> &'static str {
        "pi"
    }

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator> {
        let mut out = Vec::new();
        let mut seen = std::collections::HashSet::new();
        for file in collect(roots) {
            let sid = header_id(&file).unwrap_or_else(|| session_id_from_name(&file));
            if sid.is_empty() || !seen.insert(sid.clone()) {
                continue;
            }
            out.push(SessionLocator {
                harness: "pi".into(),
                session_id: sid,
                locator: file,
                cwd: String::new(),
            });
        }
        out
    }

    fn records(
        &self,
        locator: &Path,
        session_id: &str,
    ) -> Result<Vec<crate::store::Record>, String> {
        crate::store::jsonl_records(locator, self.id(), session_id)
    }

    fn events(&self, records: &[crate::store::Record]) -> Vec<Event> {
        timeline_rows(records)
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_file() {
            return Err(format!("pi session not found: {session_id}"));
        }
        let rows = jsonl::window(locator);
        let header = rows
            .iter()
            .find(|r| text::field_str(&r.value, "type") == "session")
            .map(|r| &r.value);
        let sid = header
            .map(|h| text::field_str(h, "id"))
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| session_id.to_string());
        let created = header
            .map(|h| text::field_iso(h, "timestamp"))
            .unwrap_or_default();
        let mut last_ts = created.clone();
        for row in &rows {
            let ts = text::field_iso(&row.value, "timestamp");
            if !ts.is_empty() {
                last_ts = ts;
            }
        }
        if last_ts.is_empty() {
            let stamp = jsonl::file_stamp(locator);
            last_ts = text::iso_secs(stamp.0 as i64);
        }
        let (tools, kids) = count_tools(&rows);
        Ok(ListMeta {
            session_id: sid,
            locator: locator.to_path_buf(),
            model_id: model_from_rows(&rows),
            title: first_user_title(&rows),
            created_at: created.clone(),
            updated_at: last_ts.clone(),
            duration_seconds: text::duration_secs(
                text::epoch_secs(&Value::String(created)),
                text::epoch_secs(&Value::String(last_ts)),
            ),
            tool_call_count: tools,
            turn_outcome: last_role_outcome(&rows),
            harness: "pi".into(),
            harness_version: header
                .map(|h| text::field_str(h, "version"))
                .unwrap_or_default(),
            run_dir: header
                .map(|h| text::field_str(h, "cwd"))
                .unwrap_or_default(),
            num_events: 0,
            has_subagents: kids > 0,
            subagent_count: kids,
            context_tokens_used: None,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(
            "../tests/fixtures/harness/pi/sessions/tmp-probe/2026-08-09T12-00-00-000Z_019fe000-0000-7000-8000-000000000001.jsonl",
        )
    }

    #[test]
    fn list_meta_window_title_and_last_turn() {
        let path = fixture();
        let meta = Pi
            .list_meta(&path, "019fe000-0000-7000-8000-000000000001")
            .unwrap();
        assert_eq!(meta.title, "Reply with PI_PROBE_OK");
        assert_eq!(meta.turn_outcome, "complete");
        assert_eq!(meta.model_id, "xai/grok-4.5");
    }
}
