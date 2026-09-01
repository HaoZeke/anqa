//! Claude Code jsonl store.

use crate::event::{Event, EventType, ListMeta, SessionLocator};
use crate::jsonl::{self, JsonlRow};
use crate::store::Store;
use crate::text;
use serde_json::Value;
use std::path::{Path, PathBuf};

pub struct Claude;

const AGENT_TOOLS: &[&str] = &["Agent", "Task", "TaskCreate", "agent", "task"];

fn collect(roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for raw in roots {
        if raw.is_file() && raw.extension().and_then(|s| s.to_str()) == Some("jsonl") {
            if !raw
                .parent()
                .is_some_and(|p| p.file_name().and_then(|n| n.to_str()) == Some("subagents"))
            {
                out.push(raw.clone());
            }
        } else if raw.is_dir() {
            for file in crate::walk::find_files(raw, ".jsonl", "") {
                if file
                    .parent()
                    .is_some_and(|p| p.file_name().and_then(|n| n.to_str()) == Some("subagents"))
                {
                    continue;
                }
                out.push(file);
            }
        }
    }
    out
}

fn sid_of(path: &Path) -> String {
    path.file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_string()
}

fn is_tool_result_user(msg: &Value) -> bool {
    msg.get("content")
        .and_then(|v| v.as_array())
        .is_some_and(|blocks| {
            blocks
                .iter()
                .any(|b| text::field_str(b, "type") == "tool_result")
        })
}

fn timeline_rows(rows: &[JsonlRow]) -> Vec<Event> {
    let mut names = std::collections::HashMap::new();
    let mut children = std::collections::HashMap::new();
    for row in rows {
        let typ = text::field_str(&row.value, "type");
        if typ == "user" {
            let tur = row
                .value
                .get("toolUseResult")
                .cloned()
                .unwrap_or(Value::Null);
            let agent = text::field_str(&tur, "agentId");
            if !agent.is_empty() {
                let atyp = text::field_str(&tur, "agentType");
                let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
                if let Some(blocks) = msg.get("content").and_then(|v| v.as_array()) {
                    for block in blocks {
                        if text::field_str(block, "type") == "tool_result" {
                            let call_id = text::field_str(block, "tool_use_id");
                            if !call_id.is_empty() {
                                children.insert(call_id, (agent.clone(), atyp.clone()));
                            }
                        }
                    }
                }
            }
        }
        if typ != "assistant" {
            continue;
        }
        let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
        if let Some(blocks) = msg.get("content").and_then(|v| v.as_array()) {
            for block in blocks {
                if text::field_str(block, "type") != "tool_use" {
                    continue;
                }
                let id = text::field_str(block, "id");
                let name = text::field_str(block, "name");
                if !id.is_empty() {
                    names.insert(id.clone(), name.clone());
                }
            }
        }
    }
    let mut events = Vec::new();
    let mut turn = 0i32;
    for row in rows {
        let typ = text::field_str(&row.value, "type");
        let ts = text::field_i64(&row.value, "timestamp");
        if typ == "user" {
            let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
            if is_tool_result_user(&msg) {
                events.push(tool_result(&row.value, ts, &names, &row.raw));
            } else {
                let text_body = text::text_of(msg.get("content").unwrap_or(&Value::Null));
                if !text_body.trim().is_empty() {
                    events.push(
                        Event::new(EventType::TurnStarted)
                            .with_ts(ts)
                            .with_content(format!("turn_number={turn}"))
                            .with_raw(&row.raw),
                    );
                    events.push(
                        Event::new(EventType::UserMessageChunk)
                            .with_ts(ts)
                            .with_content(text_body)
                            .with_raw(&row.raw),
                    );
                    turn += 1;
                }
            }
        } else if typ == "assistant" {
            events.extend(assistant(&row.value, ts, &children, &row.raw));
        }
    }
    text::index_events(&mut events);
    events
}

fn assistant(
    row: &Value,
    ts: Option<i64>,
    children: &std::collections::HashMap<String, (String, String)>,
    raw: &str,
) -> Vec<Event> {
    let mut out = Vec::new();
    let msg = row.get("message").cloned().unwrap_or(Value::Null);
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
        } else if kind == "tool_use" {
            let name = {
                let n = text::field_str(block, "name");
                if n.is_empty() {
                    "tool".into()
                } else {
                    n
                }
            };
            let call_id = text::field_str(block, "id");
            let input = block.get("input").cloned().unwrap_or(Value::Null);
            let raw_args = serde_json::to_string(&input).unwrap_or_default();
            let mut ev = Event::new(EventType::ToolCall)
                .with_ts(ts)
                .with_content(name.clone())
                .with_raw(raw_args);
            ev.tool_name = name.clone();
            ev.tool_call_id = call_id.clone();
            out.push(ev);
            if AGENT_TOOLS.contains(&name.as_str()) {
                let (child, typ) = children.get(&call_id).cloned().unwrap_or_else(|| {
                    (
                        text::field_str(&input, "agentId"),
                        text::field_str(&input, "subagent_type"),
                    )
                });
                if !child.is_empty() {
                    let desc = text::field_str(&input, "description");
                    let mut spawn = Event::new(EventType::SubagentSpawned)
                        .with_ts(ts)
                        .with_content(format!("spawned {typ}: {desc}").trim().to_string())
                        .with_raw(raw);
                    spawn.child_session_id = child;
                    spawn.subagent_type = typ;
                    spawn.description = desc;
                    out.push(spawn);
                }
            }
        }
    }
    out
}

fn tool_result(
    row: &Value,
    ts: Option<i64>,
    names: &std::collections::HashMap<String, String>,
    raw: &str,
) -> Event {
    let msg = row.get("message").cloned().unwrap_or(Value::Null);
    let mut call_id = String::new();
    let mut body = String::new();
    if let Some(blocks) = msg.get("content").and_then(|v| v.as_array()) {
        for block in blocks {
            if text::field_str(block, "type") != "tool_result" {
                continue;
            }
            call_id = text::field_str(block, "tool_use_id");
            body = text::text_of(block.get("content").unwrap_or(&Value::Null));
        }
    }
    let tur = row.get("toolUseResult").cloned().unwrap_or(Value::Null);
    if body.is_empty() {
        body = text::text_of(
            tur.get("content")
                .or_else(|| tur.get("stdout"))
                .unwrap_or(&tur),
        );
    }
    let child = text::field_str(&tur, "agentId");
    let typ = text::field_str(&tur, "agentType");
    let name = names
        .get(&call_id)
        .cloned()
        .unwrap_or_else(|| "tool".into());
    if !child.is_empty() {
        let mut ev = Event::new(EventType::SubagentFinished)
            .with_ts(ts)
            .with_content(body.chars().take(400).collect::<String>())
            .with_raw(raw);
        ev.tool_name = name;
        ev.child_session_id = child;
        ev.subagent_type = typ;
        ev.tool_call_id = call_id;
        return ev;
    }
    let mut ev = Event::new(EventType::ToolCallUpdate)
        .with_ts(ts)
        .with_content(body)
        .with_raw(raw);
    ev.tool_name = name;
    ev.tool_call_id = call_id;
    ev
}

impl Store for Claude {
    fn id(&self) -> &'static str {
        "claude"
    }

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator> {
        collect(roots)
            .into_iter()
            .filter_map(|file| {
                let sid = sid_of(&file);
                if sid.is_empty() {
                    return None;
                }
                Some(SessionLocator {
                    harness: "claude".into(),
                    session_id: sid,
                    locator: file,
                    cwd: String::new(),
                })
            })
            .collect()
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_file() {
            return Err(format!("claude session not found: {session_id}"));
        }
        let _rows = jsonl::window(locator);
        Ok(ListMeta {
            session_id: session_id.to_string(),
            locator: locator.to_path_buf(),
            model_id: "unknown".into(),
            title: String::new(),
            harness: "claude".into(),
            turn_outcome: String::new(),
            ..ListMeta::default()
        })
    }

    fn timeline(&self, locator: &Path, session_id: &str) -> Result<Vec<Event>, String> {
        if !locator.is_file() {
            return Err(format!("claude session not found: {session_id}"));
        }
        Ok(timeline_rows(&jsonl::read_objects(locator)))
    }
}
