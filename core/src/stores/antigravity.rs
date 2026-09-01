//! Antigravity conversation db + transcript jsonl.

use crate::event::{Event, EventType, SessionLocator};
use crate::jsonl;
use crate::store::Store;
use crate::text;
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
        Ok(jsonl::read_objects(&path))
    }

    fn events(&self, records: &[crate::store::Record]) -> Vec<Event> {
        timeline_rows(records)
    }
}
