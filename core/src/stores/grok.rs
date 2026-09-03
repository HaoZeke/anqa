//! Grok session directory (`updates.jsonl` + `events.jsonl`).

use crate::event::{Event, EventType, ListMeta, ListStatus, SessionLocator};
use crate::jsonl;
use crate::scan::keep_updates_line;
use crate::store::Store;
use crate::text;
use crate::walk;
use serde_json::Value;
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

fn goal_id_in_raw(raw: &str) -> String {
    let Ok(val) = serde_json::from_str::<Value>(raw) else {
        return String::new();
    };
    let gid = text::field_str(&val, "goal_id");
    if gid.is_empty() {
        text::field_str(&val, "goalId")
    } else {
        gid
    }
}

fn subagent_finish_content(update: &Value) -> String {
    let desc = text::field_str(update, "description");
    if !desc.is_empty() {
        return desc;
    }
    text::field_str(update, "status")
}

fn task_preview(update: &Value) -> String {
    let command = text::field_str(update, "command");
    let command = if command.is_empty() {
        text::field_str(update, "display_command")
    } else {
        command
    };
    if !command.is_empty() {
        return format!("$ {}", command.replace('\n', " "));
    }
    let desc = text::field_str(update, "description");
    if !desc.is_empty() {
        return desc;
    }
    let snap = update.get("task_snapshot");
    if let Some(obj) = snap {
        let command = text::field_str(obj, "command");
        if !command.is_empty() {
            return format!("$ {}", command.replace('\n', " "));
        }
        let out = text::field_str(obj, "output");
        if !out.is_empty() {
            return text::first_line(&out, 160);
        }
    }
    String::new()
}

fn session_update_content(kind: &EventType, update: &Value) -> String {
    match kind {
        EventType::SessionRecap => text::field_str(update, "summary"),
        EventType::GoalUpdated => {
            let objective = text::field_str(update, "objective");
            let status = text::field_str(update, "status");
            let phase = text::field_str(update, "phase");
            let last = text::field_str(update, "last_event");
            let verdict = text::field_str(update, "last_classifier_verdict");
            let mut bits = Vec::new();
            if !objective.is_empty() {
                bits.push(objective);
            }
            let mut mid = Vec::new();
            if !status.is_empty() {
                mid.push(format!("status={status}"));
            }
            if !phase.is_empty() {
                mid.push(format!("phase={phase}"));
            }
            if !last.is_empty() {
                mid.push(format!("last={last}"));
            }
            if !verdict.is_empty() {
                mid.push(format!("verdict={verdict}"));
            }
            if !mid.is_empty() {
                bits.push(mid.join(" "));
            }
            let detail = text::field_str(update, "last_event_detail");
            if !detail.is_empty() {
                bits.push(detail.chars().take(240).collect());
            }
            if bits.is_empty() {
                "goal_updated".into()
            } else {
                bits.join("  ")
            }
        }
        EventType::AutoCompactStarted => compact_started_content(update),
        EventType::AutoCompactCompleted => compact_completed_content(update),
        EventType::CompactionCheckpoint => compact_checkpoint_content(update),
        EventType::HookExecution => hook_execution_row(update).0,
        EventType::HookAnnotation => {
            let msg = text::field_str(update, "message");
            if msg.is_empty() {
                "hook_annotation".into()
            } else {
                text::first_line(&msg, 500)
            }
        }
        k if k.is_scheduled_task() => {
            let sched = text::field_str(update, "human_schedule");
            let prompt = text::field_str(update, "prompt");
            let mut bits = Vec::new();
            if !sched.is_empty() {
                bits.push(sched);
            }
            if !prompt.is_empty() {
                bits.push(text::first_line(&prompt, 48));
            }
            if bits.is_empty() {
                text::field_str(update, "task_id")
            } else {
                bits.join(" · ")
            }
        }
        _ => text::text_of(update.get("content").unwrap_or(&Value::Null)),
    }
}

fn hook_short_name(name: &str) -> String {
    let head = name.split(':').next().unwrap_or(name);
    head.rsplit('/').next().unwrap_or(head).to_string()
}

fn hook_execution_row(update: &Value) -> (String, bool) {
    let mut bits = Vec::new();
    let event_name = text::field_str(update, "event_name");
    if !event_name.is_empty() {
        bits.push(event_name);
    }
    let tool = text::field_str(update, "tool_name");
    if !tool.is_empty() {
        bits.push(tool);
    }
    let mut failed = false;
    let mut error = String::new();
    if let Some(Value::Array(runs)) = update.get("runs") {
        for run in runs {
            let name = text::field_str(run, "name");
            let short = if name.is_empty() {
                "hook".into()
            } else {
                hook_short_name(&name)
            };
            let st = run.get("status");
            let status = match st {
                Some(Value::Object(obj)) => obj.get("status").map(text::as_str).unwrap_or_default(),
                Some(other) => text::as_str(other),
                None => String::new(),
            };
            let blocked = st
                .and_then(|v| v.get("blocked"))
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let run_error = st
                .and_then(|v| v.get("error"))
                .map(text::as_str)
                .unwrap_or_default();
            let outcome = if blocked {
                "blocked"
            } else if status.is_empty() {
                "ran"
            } else {
                status.as_str()
            };
            bits.push(format!("{short}:{outcome}"));
            failed = failed || blocked || matches!(status.as_str(), "failed" | "error");
            if error.is_empty() && !run_error.is_empty() {
                error = run_error.chars().take(200).collect();
            }
        }
    }
    if !error.is_empty() {
        bits.push(error);
    }
    let content = if bits.is_empty() {
        "hook_execution".into()
    } else {
        bits.join("  ")
    };
    (content, failed)
}

fn compact_started_content(update: &Value) -> String {
    let mut bits = Vec::new();
    let reason = text::field_str(update, "reason");
    if !reason.is_empty() {
        bits.push(reason);
    }
    if let Some(pct) = update.get("percentage") {
        let s = text::as_str(pct);
        if !s.is_empty() {
            bits.push(format!("{s}%"));
        }
    }
    if let (Some(used), Some(window)) = (update.get("tokens_used"), update.get("context_window")) {
        bits.push(format!("{}/{}", text::as_str(used), text::as_str(window)));
    }
    if bits.is_empty() {
        "auto_compact_started".into()
    } else {
        bits.join("  ")
    }
}

fn compact_completed_content(update: &Value) -> String {
    let mut bits = Vec::new();
    if let (Some(before), Some(after)) = (update.get("tokens_before"), update.get("tokens_after")) {
        bits.push(format!(
            "{} -> {}",
            text::as_str(before),
            text::as_str(after)
        ));
    }
    if let Some(elapsed) = update.get("elapsed_ms") {
        let s = text::as_str(elapsed);
        if !s.is_empty() {
            bits.push(format!("{s}ms"));
        }
    }
    let preview = text::field_str(update, "summary_preview");
    if !preview.is_empty() {
        bits.push(preview.chars().take(240).collect());
    }
    if bits.is_empty() {
        "auto_compact_completed".into()
    } else {
        bits.join("  ")
    }
}

fn compact_checkpoint_content(update: &Value) -> String {
    let mut bits = Vec::new();
    let cid = text::field_str(update, "checkpoint_id");
    if !cid.is_empty() {
        bits.push(cid);
    }
    if let Some(pidx) = update.get("prompt_index_at_compaction") {
        let s = text::as_str(pidx);
        if !s.is_empty() {
            bits.push(format!("prompt_index={s}"));
        }
    }
    if bits.is_empty() {
        "compaction_checkpoint".into()
    } else {
        bits.join("  ")
    }
}

fn decode_b64(input: &str) -> Option<Vec<u8>> {
    let s: String = input.chars().filter(|c| !c.is_ascii_whitespace()).collect();
    if s.is_empty() {
        return None;
    }
    fn val(c: u8) -> Option<u8> {
        match c {
            b'A'..=b'Z' => Some(c - b'A'),
            b'a'..=b'z' => Some(c - b'a' + 26),
            b'0'..=b'9' => Some(c - b'0' + 52),
            b'+' | b'-' => Some(62),
            b'/' | b'_' => Some(63),
            b'=' => Some(0),
            _ => None,
        }
    }
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len() * 3 / 4);
    let mut i = 0;
    while i < bytes.len() {
        let a = val(bytes[i])?;
        let b = val(*bytes.get(i + 1)?)?;
        let cch = bytes.get(i + 2).copied().unwrap_or(b'=');
        let dch = bytes.get(i + 3).copied().unwrap_or(b'=');
        let c = val(cch)?;
        let d = val(dch)?;
        out.push((a << 2) | (b >> 4));
        if cch != b'=' {
            out.push((b << 4) | (c >> 2));
        }
        if dch != b'=' {
            out.push((c << 6) | d);
        }
        i += 4;
    }
    if out.is_empty() {
        None
    } else {
        Some(out)
    }
}

fn message_parts(val: &Value) -> (String, Vec<Vec<u8>>) {
    match val {
        Value::String(s) => (s.clone(), Vec::new()),
        Value::Array(items) => {
            let mut text = String::new();
            let mut images = Vec::new();
            for item in items {
                let (chunk, imgs) = message_parts(item);
                text.push_str(&chunk);
                images.extend(imgs);
            }
            (text, images)
        }
        Value::Object(obj) => {
            let kind = obj.get("type").map(text::as_str).unwrap_or_default();
            if kind == "image" {
                let data = obj.get("data").map(text::as_str).unwrap_or_default();
                (String::new(), decode_b64(&data).into_iter().collect())
            } else {
                (text::field_str(val, "text"), Vec::new())
            }
        }
        _ => (String::new(), Vec::new()),
    }
}

fn child_session_id(update: &Value) -> String {
    let cid = text::field_str(update, "childSessionId");
    if !cid.is_empty() {
        return cid;
    }
    let cid = text::field_str(update, "child_session_id");
    if !cid.is_empty() {
        return cid;
    }
    text::field_str(update, "subagentId")
}

fn web_search_from_action(action: &Value) -> (String, String, String) {
    if !action.is_object() {
        return (String::new(), String::new(), String::new());
    }
    let query = text::field_str(action, "query");
    let page_url = {
        let u = text::field_str(action, "url");
        if u.is_empty() {
            text::field_str(action, "link")
        } else {
            u
        }
    };
    let mut lines = Vec::new();
    if !query.is_empty() {
        lines.push(query.clone());
    }
    let mut urls = Vec::new();
    if let Some(Value::Array(sources)) = action.get("sources") {
        for item in sources {
            let url = match item {
                Value::Object(_) => {
                    let u = text::field_str(item, "url");
                    if u.is_empty() {
                        text::field_str(item, "link")
                    } else {
                        u
                    }
                }
                Value::String(s) => s.clone(),
                _ => String::new(),
            };
            if url.is_empty() {
                continue;
            }
            let title = if item.is_object() {
                text::field_str(item, "title")
            } else {
                String::new()
            };
            urls.push(if title.is_empty() {
                url
            } else {
                format!("{title}  {url}")
            });
        }
    }
    if !page_url.is_empty() && !urls.iter().any(|u| u.contains(&page_url)) {
        urls.push(page_url.clone());
    }
    if !urls.is_empty() {
        if !lines.is_empty() {
            lines.push(String::new());
        }
        lines.extend(urls);
    }
    (lines.join("\n"), query, page_url)
}

fn web_search_from_raw(raw_output: &Value) -> (String, String, String) {
    if !raw_output.is_object() {
        return (String::new(), String::new(), String::new());
    }
    let from_action = web_search_from_action(raw_output.get("action").unwrap_or(&Value::Null));
    if !from_action.0.is_empty() || !from_action.1.is_empty() || !from_action.2.is_empty() {
        return from_action;
    }
    web_search_from_action(raw_output)
}

fn merge_json_fields(raw: &str, pairs: &[(&str, &str)]) -> String {
    let mut obj = match serde_json::from_str::<Value>(raw) {
        Ok(Value::Object(map)) => map,
        _ => serde_json::Map::new(),
    };
    for (key, val) in pairs {
        if val.is_empty() {
            continue;
        }
        if !obj.contains_key(*key) {
            obj.insert((*key).to_string(), Value::String((*val).to_string()));
        }
    }
    serde_json::to_string(&Value::Object(obj)).unwrap_or_else(|_| raw.to_string())
}

fn merge_search_and_workflow(ev: &mut Event, query: &str, url: &str, raw_output: &Value) {
    let mut pairs: Vec<(&str, String)> = Vec::new();
    if !query.is_empty() {
        pairs.push(("query", query.to_string()));
    }
    if !url.is_empty() {
        pairs.push(("url", url.to_string()));
    }
    let kind = text::field_str(raw_output, "type");
    if ev.tool_name == "workflow" || kind == "Workflow" {
        let rid = text::field_str(raw_output, "run_id");
        let name = text::field_str(raw_output, "name");
        if !rid.is_empty() {
            pairs.push(("run_id", rid));
        }
        if !name.is_empty() {
            pairs.push(("name", name));
        }
    }
    if pairs.is_empty() {
        return;
    }
    let refs: Vec<(&str, &str)> = pairs.iter().map(|(k, v)| (*k, v.as_str())).collect();
    ev.raw = merge_json_fields(&ev.raw, &refs);
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
    rec: &crate::store::Record,
    events: &mut Vec<Event>,
    results: &mut std::collections::HashMap<String, usize>,
    pending: &mut std::collections::HashMap<String, usize>,
) {
    let val = &rec.value;
    let line = rec.raw.as_str();
    let update = val
        .pointer("/params/update")
        .cloned()
        .unwrap_or(Value::Null);
    let kind = EventType::parse(&text::field_str(&update, "sessionUpdate"));
    let ts = text::epoch(val);
    match kind {
        EventType::UserMessageChunk
        | EventType::AgentMessageChunk
        | EventType::AgentThoughtChunk => {
            let mapped = kind;
            let (content, images) = message_parts(update.get("content").unwrap_or(&Value::Null));
            if let Some(prev) = events.last_mut() {
                if prev.event_type == mapped {
                    if mapped != EventType::UserMessageChunk {
                        prev.content.push_str(&content);
                        prev.images.extend(images);
                        prev.timestamp = ts.or(prev.timestamp);
                        if prev.prompt_index.is_none() {
                            prev.prompt_index = prompt_index(&update);
                        }
                        return;
                    }
                    let old = prev.content.clone();
                    let merge = content.is_empty()
                        || old.is_empty()
                        || content.starts_with(&old)
                        || old.starts_with(&content);
                    if merge {
                        prev.images.extend(images);
                        if !content.is_empty() && content.len() >= old.len() {
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
            ev.images = images;
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
            ev.tool_call_id = call_id.clone();
            pending.insert(call_id, events.len());
            events.push(ev);
        }
        EventType::ToolCallUpdate => {
            let call_id = text::field_str(&update, "toolCallId");
            let mut body = text::text_of(update.get("content").unwrap_or(&Value::Null));
            let raw_output = update.get("rawOutput").cloned().unwrap_or(Value::Null);
            if body.is_empty() {
                body = text::text_of(&raw_output);
            }
            let (search_body, search_query, search_url) = web_search_from_raw(&raw_output);
            if !search_body.is_empty() && (body.is_empty() || body.trim() == "{}") {
                body = search_body;
            }
            let failed = update.get("isError") == Some(&Value::Bool(true))
                || text::field_str(&update, "status") == "failed";
            let terminal = failed
                || matches!(
                    text::field_str(&update, "status").as_str(),
                    "completed" | "failed"
                );
            if body.is_empty() && !failed && !terminal {
                return;
            }
            let mut tool_name = String::new();
            if let Some(&idx) = pending.get(&call_id) {
                if let Some(ev) = events.get_mut(idx) {
                    tool_name = ev.tool_name.clone();
                    merge_search_and_workflow(ev, &search_query, &search_url, &raw_output);
                    if failed {
                        ev.is_error = true;
                    }
                }
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
                    if ev.tool_name.is_empty() {
                        ev.tool_name = tool_name;
                    }
                }
                return;
            }
            if body.is_empty() && !failed {
                return;
            }
            let mut ev = Event::new(EventType::ToolCallUpdate)
                .with_ts(ts)
                .with_content(body)
                .with_raw(update_raw(&update, line));
            ev.tool_name = if tool_name.is_empty() {
                text::field_str(&update, "toolName")
            } else {
                tool_name
            };
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
            ev.child_session_id = child_session_id(&update);
            ev.subagent_type = text::field_str(&update, "subagentType");
            ev.description = text::field_str(&update, "description");
            events.push(ev);
        }
        EventType::SubagentFinished => {
            let mut ev = Event::new(EventType::SubagentFinished)
                .with_ts(ts)
                .with_content(subagent_finish_content(&update))
                .with_raw(update_raw(&update, line));
            ev.child_session_id = child_session_id(&update);
            events.push(ev);
        }
        EventType::HookExecution => {
            let (content, failed) = hook_execution_row(&update);
            let mut ev = Event::new(EventType::HookExecution)
                .with_ts(ts)
                .with_content(content)
                .with_raw(update_raw(&update, line));
            ev.is_error = failed;
            events.push(ev);
        }
        EventType::TaskBackgrounded => {
            let mut ev = Event::new(EventType::TaskBackgrounded)
                .with_ts(ts)
                .with_content(task_preview(&update))
                .with_raw(task_family_raw(&update, line));
            ev.tool_call_id = text::field_str(&update, "tool_call_id");
            events.push(ev);
        }
        EventType::TaskCompleted => {
            let mut ev = Event::new(EventType::TaskCompleted)
                .with_ts(ts)
                .with_content(task_preview(&update))
                .with_raw(task_family_raw(&update, line));
            ev.tool_call_id = text::field_str(&update, "tool_call_id");
            events.push(ev);
        }
        EventType::TurnCompleted => {
            events.push(
                Event::new(EventType::TurnCompleted)
                    .with_ts(ts)
                    .with_raw(update_raw(&update, line)),
            );
        }
        EventType::GoalUpdated => {
            let gid = text::field_str(&update, "goal_id");
            let gid = if gid.is_empty() {
                text::field_str(&update, "goalId")
            } else {
                gid
            };
            if let Some(prev) = events.last_mut() {
                if prev.event_type == EventType::GoalUpdated && goal_id_in_raw(&prev.raw) == gid {
                    prev.content = session_update_content(&EventType::GoalUpdated, &update);
                    prev.raw = update_raw(&update, line);
                    prev.timestamp = ts.or(prev.timestamp);
                    return;
                }
            }
            events.push(
                Event::new(EventType::GoalUpdated)
                    .with_ts(ts)
                    .with_content(session_update_content(&EventType::GoalUpdated, &update))
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
            let mut ev = Event::new(other.clone())
                .with_ts(ts)
                .with_content(session_update_content(&other, &update))
                .with_raw(raw);
            ev.tool_call_id = text::field_str(&update, "tool_call_id");
            events.push(ev);
        }
    }
}

/// Same needles as ``anqa.harness.grok_parse._LIST_MARKER_NEEDLES``.
const LIST_MARKER_NEEDLES: &[&[u8]] = &[
    br#""turn_started""#,
    br#""turn_ended""#,
    br#""loop_started""#,
    br#""session_error""#,
    br#""turn_error""#,
    br#""fatal_error""#,
    br#""type":"error""#,
    br#""type": "error""#,
];

fn keep_events_line(line: &[u8]) -> bool {
    LIST_MARKER_NEEDLES
        .iter()
        .copied()
        .any(|needle| memchr::memmem::find(line, needle).is_some())
}

fn map_events_row(row: &crate::store::Record, events: &mut Vec<Event>) {
    let kind = EventType::parse(&text::field_str(&row.value, "type"));
    if !kind.is_turn_marker() {
        return;
    }
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
            let outcome = turn_end_outcome(&row.value);
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

fn session_update_token(line: &[u8]) -> Option<&str> {
    const KEY: &[u8] = br#""sessionUpdate""#;
    let at = memchr::memmem::find(line, KEY)?;
    let rest = line.get(at + KEY.len()..)?;
    let mut i = 0;
    while i < rest.len() && rest[i].is_ascii_whitespace() {
        i += 1;
    }
    if rest.get(i) != Some(&b':') {
        return None;
    }
    i += 1;
    while i < rest.len() && rest[i].is_ascii_whitespace() {
        i += 1;
    }
    if rest.get(i) != Some(&b'"') {
        return None;
    }
    i += 1;
    let start = i;
    while i < rest.len() && rest[i] != b'"' {
        i += 1;
    }
    std::str::from_utf8(rest.get(start..i)?).ok()
}

fn keep_line(line: &[u8]) -> bool {
    if !keep_updates_line(line) {
        return false;
    }
    match session_update_token(line) {
        None => true,
        Some(token) => !matches!(EventType::parse(token), EventType::Other(_)),
    }
}

fn read_updates(dir: &Path) -> Vec<crate::store::Record> {
    jsonl::cached_records(&dir.join("updates.jsonl"), Some(keep_line))
}

fn read_events(dir: &Path) -> Vec<crate::store::Record> {
    jsonl::cached_records(&dir.join("events.jsonl"), Some(keep_events_line))
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
        rows.extend(read_events(locator));
        Ok(rows)
    }

    fn events(&self, records: &[crate::store::Record]) -> Vec<Event> {
        let mut events = Vec::new();
        let mut results = std::collections::HashMap::new();
        let mut pending = std::collections::HashMap::new();
        for rec in records {
            if rec.value.pointer("/params/update").is_some() {
                consume_line(rec, &mut events, &mut results, &mut pending);
            } else {
                map_events_row(rec, &mut events);
            }
        }
        events.sort_by_key(|ev| {
            let ts = ev.timestamp.unwrap_or(i64::MAX);
            let start = u8::from(ev.event_type == EventType::TurnStarted);
            (ts, ev.update_index, start, ev.index)
        });
        drop_empty_turn_starts(&mut events);
        events
    }

    fn timeline(&self, locator: &Path, session_id: &str) -> Result<Vec<Event>, String> {
        let records = self.records(locator, session_id)?;
        let mut events = self.events(&records);
        prepend_system_prompt(locator, &mut events);
        Event::carry_turn_numbers(&mut events);
        text::index_events(&mut events);
        Ok(events)
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_dir() {
            return Err(format!("grok session not found: {session_id}"));
        }
        let mut meta = list_meta_of(locator, session_id);
        if summary_message_count(locator).is_none() {
            meta.num_events = self.event_count(locator, session_id);
        }
        Ok(meta)
    }

    fn stamp(&self, locator: &Path, session_id: &str) -> crate::event::FileStamp {
        let _ = session_id;
        jsonl::pair_stamp(
            &locator.join("updates.jsonl"),
            &locator.join("events.jsonl"),
        )
    }
}

fn read_json(path: &Path) -> Value {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null)
}

fn one_line(raw: &str) -> String {
    text::first_line(&raw.split_whitespace().collect::<Vec<_>>().join(" "), 80)
}

fn summary_message_count(dir: &Path) -> Option<i64> {
    text::field_i64(&read_json(&dir.join("summary.json")), "num_messages")
}

fn apply_summary(meta: &mut ListMeta, dir: &Path) {
    let data = read_json(&dir.join("summary.json"));
    if data.is_null() {
        return;
    }
    let model = text::field_str(&data, "current_model_id");
    if !model.is_empty() {
        meta.model_id = model;
    }
    let title = text::field_str(&data, "generated_title");
    let title = if title.is_empty() {
        text::field_str(&data, "session_summary")
    } else {
        title
    };
    if !title.is_empty() && !title.trim_start().starts_with('<') {
        meta.title = one_line(&title);
    }
    meta.created_at = text::field_str(&data, "created_at");
    meta.updated_at = text::field_str(&data, "updated_at");
    if let Some(n) = text::field_i64(&data, "num_messages") {
        meta.num_events = n.max(0) as u32;
    }
    if let Some(info) = data.get("info") {
        let tid = text::field_str(info, "task_id");
        if !tid.is_empty() {
            meta.task_id = tid;
        }
    }
}

fn apply_goal_title(meta: &mut ListMeta, dir: &Path) {
    if !meta.title.is_empty() {
        return;
    }
    let data = read_json(&dir.join("goal").join("state.json"));
    let objective = text::field_str(&data, "objective");
    if !objective.is_empty() {
        meta.title = one_line(&objective);
    }
}

fn json_f64(obj: &Value, key: &str) -> Option<f64> {
    let val = obj.get(key)?;
    val.as_f64()
        .or_else(|| val.as_i64().map(|n| n as f64))
        .or_else(|| val.as_str()?.parse().ok())
}

fn apply_signals(meta: &mut ListMeta, dir: &Path) {
    let sig = read_json(&dir.join("signals.json"));
    if sig.is_null() {
        return;
    }
    if let Some(n) = text::field_i64(&sig, "toolCallCount") {
        meta.tool_call_count = n.max(0) as u32;
    }
    if let Some(n) = json_f64(&sig, "sessionDurationSeconds") {
        meta.duration_seconds = n.max(0.0);
    }
    let mid = text::field_str(&sig, "primaryModelId");
    let mid = if mid.is_empty() {
        sig.get("modelsUsed")
            .and_then(Value::as_array)
            .and_then(|a| a.first())
            .map(text::as_str)
            .unwrap_or_default()
    } else {
        mid
    };
    if !mid.is_empty()
        && (meta.model_id.is_empty()
            || matches!(meta.model_id.as_str(), "unknown" | "v9" | "grok-build"))
    {
        meta.model_id = mid;
    }
    if let Some(n) = text::field_i64(&sig, "contextTokensUsed") {
        meta.context_tokens_used = Some(n.max(0));
    }
    if let Some(n) = text::field_i64(&sig, "contextWindowUsage") {
        meta.context_window_usage_pct = Some(n.max(0));
    }
    if let Some(n) = text::field_i64(&sig, "contextWindowTokens") {
        if n > 0 {
            meta.context_window_tokens = Some(n);
        }
    }
    if let Some(n) = text::field_i64(&sig, "turnCount") {
        meta.turn_count = n.max(0) as u32;
    }
    if let Some(n) = text::field_i64(&sig, "errorCount") {
        meta.error_count = n.max(0) as u32;
    }
    if let Some(n) = text::field_i64(&sig, "toolFailureCount") {
        meta.tool_failure_count = n.max(0) as u32;
    }
    if let Some(n) = text::field_i64(&sig, "agentLinesAdded") {
        meta.lines_added = n.max(0) as u32;
    }
    if let Some(n) = text::field_i64(&sig, "agentLinesRemoved") {
        meta.lines_removed = n.max(0) as u32;
    }
    if let Some(n) = text::field_i64(&sig, "compactionCount") {
        meta.compaction_count = n.max(0) as u32;
    }
    if let Some(n) = text::field_i64(&sig, "doomLoopWarnings") {
        meta.doom_loop_warnings = n.max(0) as u32;
    }
}

fn apply_run(meta: &mut ListMeta, dir: &Path) {
    let run = read_json(&dir.join("run.json"));
    if run.is_null() {
        return;
    }
    let mid = text::field_str(&run, "model");
    if !mid.is_empty()
        && (meta.model_id.is_empty()
            || matches!(meta.model_id.as_str(), "unknown" | "v9" | "grok-build"))
    {
        meta.model_id = mid;
    }
    let tid = text::field_str(&run, "task_id");
    if !tid.is_empty() {
        meta.task_id = tid;
    }
}

const LIST_TURN_UPDATES: &[&str] = &[
    "turn_completed",
    "session_recap",
    "turn_ended",
    "turn_started",
    "user_message_chunk",
    "agent_message_chunk",
    "tool_call",
    "tool_call_update",
    "plan",
];

const OPEN_TURN_UPDATES: &[&str] = &[
    "agent_message_chunk",
    "tool_call",
    "tool_call_update",
    "plan",
];

fn updates_tail_types(dir: &Path) -> Vec<String> {
    jsonl::tail(&dir.join("updates.jsonl"))
        .into_iter()
        .filter_map(|row| {
            let update = row.value.pointer("/params/update")?;
            let kind = text::field_str(update, "sessionUpdate");
            if kind.is_empty() {
                None
            } else {
                Some(kind)
            }
        })
        .collect()
}

fn outcome_from_update_types(types: &[String]) -> (String, bool) {
    let mut status = String::new();
    let mut opened = false;
    let mut reopened = false;
    for etype in types {
        if !LIST_TURN_UPDATES.contains(&etype.as_str()) {
            continue;
        }
        if ListStatus::from_token(etype) == ListStatus::Complete {
            status = "completed".into();
            opened = false;
        } else if etype == "user_message_chunk" || etype == "turn_started" {
            reopened = opened || status == "completed";
            status.clear();
            opened = true;
        } else if OPEN_TURN_UPDATES.contains(&etype.as_str()) && (opened || status != "completed") {
            status = "running".into();
        }
    }
    (status, reopened)
}

fn turn_end_outcome(ev: &Value) -> String {
    let outcome = text::field_str(ev, "outcome");
    let outcome = if outcome.is_empty() {
        text::field_str(ev, "status")
    } else {
        outcome
    };
    let category = text::field_str(ev, "cancellation_category");
    let trigger = ev
        .get("cancellation_context")
        .map(|ctx| text::field_str(ctx, "trigger"))
        .unwrap_or_default();
    if category == "mid_turn_abort" && trigger == "send_now" {
        return "interjected".into();
    }
    if outcome.is_empty() {
        "unknown".into()
    } else {
        outcome
    }
}

fn events_runtime(dir: &Path) -> (String, bool) {
    let rows = jsonl::cached_records(&dir.join("events.jsonl"), Some(keep_events_line));
    let mut turn_outcome = String::new();
    let mut open_starts: i32 = 0;
    for row in rows {
        let et = text::field_str(&row.value, "type");
        match et.as_str() {
            "turn_started" => open_starts += 1,
            "turn_ended" => {
                turn_outcome = turn_end_outcome(&row.value);
                open_starts = (open_starts - 1).max(0);
            }
            "error" | "session_error" | "turn_error" | "fatal_error" if turn_outcome.is_empty() => {
                turn_outcome = "error".into();
            }
            _ => {}
        }
    }
    (turn_outcome, open_starts > 0)
}

fn list_turn_outcome(dir: &Path) -> String {
    let types = updates_tail_types(dir);
    let (from_updates, reopened) = outcome_from_update_types(&types);
    let last = types
        .iter()
        .rev()
        .find(|t| LIST_TURN_UPDATES.contains(&t.as_str()))
        .cloned()
        .unwrap_or_default();
    if from_updates == "completed" {
        return from_updates;
    }
    if from_updates == "running" {
        if reopened {
            return from_updates;
        }
        let (outcome, has_open) = events_runtime(dir);
        if !has_open && !outcome.is_empty() {
            return outcome;
        }
        return from_updates;
    }
    if !last.is_empty() {
        return String::new();
    }
    let (outcome, has_open) = events_runtime(dir);
    if has_open {
        return String::new();
    }
    if !outcome.is_empty() {
        return outcome;
    }
    if dir.join("anqa-interrupted.json").is_file() {
        return "interrupted".into();
    }
    String::new()
}

fn is_turn_started(ev: &Event) -> bool {
    ev.event_type == EventType::TurnStarted
}

fn is_turn_ended(ev: &Event) -> bool {
    ev.event_type == EventType::TurnEnded
}

fn is_turn_marker(ev: &Event) -> bool {
    matches!(
        ev.event_type,
        EventType::TurnStarted | EventType::TurnEnded | EventType::TurnCompleted
    )
}

fn drop_empty_turn_starts(events: &mut Vec<Event>) {
    if events.is_empty() {
        return;
    }
    let has_completed = events.iter().any(is_turn_ended);
    if !has_completed {
        return;
    }
    let n = events.len();
    let mut drop_at = vec![false; n];
    for i in 0..n {
        if !is_turn_started(&events[i]) {
            continue;
        }
        let mut has_work = false;
        for nxt in events.iter().take(n).skip(i + 1) {
            if is_turn_started(nxt) {
                break;
            }
            if is_turn_marker(nxt) {
                break;
            }
            has_work = true;
            break;
        }
        if !has_work {
            drop_at[i] = true;
        }
    }
    let mut i = 0;
    events.retain(|_| {
        let keep = !drop_at[i];
        i += 1;
        keep
    });
}

fn prepend_system_prompt(dir: &Path, events: &mut Vec<Event>) {
    let text = std::fs::read_to_string(dir.join("system_prompt.txt")).unwrap_or_default();
    let text = text.trim();
    if text.is_empty() {
        return;
    }
    events.insert(0, Event::new(EventType::System).with_content(text));
}

fn list_meta_of(dir: &Path, session_id: &str) -> ListMeta {
    let mut meta = ListMeta::for_session("grok", dir, session_id);
    apply_summary(&mut meta, dir);
    apply_goal_title(&mut meta, dir);
    apply_signals(&mut meta, dir);
    apply_run(&mut meta, dir);
    meta.turn_outcome = list_turn_outcome(dir);
    meta
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

    fn grok_dir(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "anqa-grok-{label}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn user_line(ts: u64, content: &str) -> String {
        format!(
            r#"{{"timestamp":{ts},"params":{{"update":{{"sessionUpdate":"user_message_chunk","content":"{content}"}}}}}}"#
        )
    }

    fn record_types(rows: &[crate::store::Record]) -> Vec<&str> {
        rows.iter()
            .filter_map(|row| row.value.get("type").and_then(|v| v.as_str()))
            .collect()
    }

    fn user_contents(rows: &[crate::store::Record]) -> Vec<&str> {
        rows.iter()
            .filter_map(|row| {
                row.value
                    .pointer("/params/update/content")
                    .and_then(|v| v.as_str())
            })
            .collect()
    }

    #[test]
    fn updates_append_and_truncate_skips_runtime_rows() {
        let dir = grok_dir("two-file");
        let mut u = fs::File::create(dir.join("updates.jsonl")).unwrap();
        writeln!(u, "{}", user_line(100, "hi")).unwrap();
        writeln!(
            u,
            r#"{{"timestamp":150,"params":{{"update":{{"sessionUpdate":"tool_call_update","toolCallId":"c1","content":"{}"}}}}}}"#,
            "x".repeat(256)
        )
        .unwrap();
        drop(u);
        let mut e = fs::File::create(dir.join("events.jsonl")).unwrap();
        for typ in [
            r#"{"ts":"1970-01-01T00:01:30Z","type":"turn_started","turn_number":0}"#,
            r#"{"ts":"1970-01-01T00:01:31Z","type":"loop_started","loop_index":0}"#,
            r#"{"ts":"1970-01-01T00:01:32Z","type":"first_token"}"#,
            r#"{"ts":"1970-01-01T00:01:33Z","type":"phase_changed","phase":"act"}"#,
            r#"{"ts":"1970-01-01T00:03:30Z","type":"turn_ended","outcome":"completed"}"#,
        ] {
            writeln!(e, "{typ}").unwrap();
        }
        drop(e);

        let rows = Grok.records(&dir, "sess").unwrap();
        assert!(
            rows.iter().all(|row| !row.raw.contains("tool_call_update")),
            "fat non-terminal tool_call_update must not appear in records"
        );
        assert_eq!(
            record_types(&rows),
            ["turn_started", "loop_started", "turn_ended"]
        );
        assert_eq!(user_contents(&rows), ["hi"]);

        let evs = Grok.timeline(&dir, "sess").unwrap();
        let types: Vec<_> = evs.iter().map(|ev| ev.event_type.as_str()).collect();
        assert_eq!(types, ["turn_started", "user_message_chunk", "turn_ended"]);

        let mut u = fs::OpenOptions::new()
            .append(true)
            .open(dir.join("updates.jsonl"))
            .unwrap();
        writeln!(u, "{}", user_line(400, "again")).unwrap();
        drop(u);

        let rows = Grok.records(&dir, "sess").unwrap();
        assert_eq!(user_contents(&rows), ["hi", "again"]);
        assert_eq!(
            record_types(&rows),
            ["turn_started", "loop_started", "turn_ended"]
        );
        let evs = Grok.timeline(&dir, "sess").unwrap();
        let users: Vec<_> = evs
            .iter()
            .filter(|ev| ev.event_type == EventType::UserMessageChunk)
            .map(|ev| ev.content.as_str())
            .collect();
        assert_eq!(users, ["hi", "again"]);

        fs::write(
            dir.join("updates.jsonl"),
            format!("{}\n", user_line(500, "fresh")),
        )
        .unwrap();
        let rows = Grok.records(&dir, "sess").unwrap();
        assert_eq!(
            user_contents(&rows),
            ["fresh"],
            "truncate must drop stale update rows"
        );
        assert_eq!(
            record_types(&rows),
            ["turn_started", "loop_started", "turn_ended"]
        );
        let evs = Grok.timeline(&dir, "sess").unwrap();
        let users: Vec<_> = evs
            .iter()
            .filter(|ev| ev.event_type == EventType::UserMessageChunk)
            .map(|ev| ev.content.as_str())
            .collect();
        assert_eq!(users, ["fresh"]);

        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn keep_line_skips_runtime_session_update() {
        let phase = br#"{"params":{"update":{"sessionUpdate":"phase_changed","phase":"act"}}}"#;
        let first = br#"{"params":{"update":{"sessionUpdate":"first_token"}}}"#;
        let user =
            br#"{"params":{"update":{"sessionUpdate":"user_message_chunk","content":"hi"}}}"#;
        let tool = br#"{"params":{"update":{"sessionUpdate":"tool_call","toolName":"read_file"}}}"#;
        assert!(!keep_line(phase));
        assert!(!keep_line(first));
        assert!(keep_line(user));
        assert!(keep_line(tool));
    }

    #[test]
    fn list_meta_title_and_completed_turn() {
        let dir = grok_dir("list-meta");
        fs::write(
            dir.join("summary.json"),
            r#"{"generated_title":"Snapshot minimal"}"#,
        )
        .unwrap();
        fs::write(
            dir.join("updates.jsonl"),
            r#"{"params":{"update":{"sessionUpdate":"turn_completed"}}}"#,
        )
        .unwrap();
        let meta = Grok.list_meta(&dir, "sess").unwrap();
        assert_eq!(meta.title, "Snapshot minimal");
        assert_eq!(meta.turn_outcome, "completed");
        assert_eq!(meta.harness, "grok");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn list_meta_reads_completed_from_updates_tail_not_header() {
        let dir = grok_dir("list-meta-tail");
        let mut u = fs::File::create(dir.join("updates.jsonl")).unwrap();
        writeln!(
            u,
            r#"{{"params":{{"update":{{"sessionUpdate":"user_message_chunk","content":"hi"}}}}}}"#
        )
        .unwrap();
        writeln!(
            u,
            r#"{{"params":{{"update":{{"sessionUpdate":"tool_call","toolName":"read_file"}}}}}}"#
        )
        .unwrap();
        let pad = format!(
            r#"{{"params":{{"update":{{"sessionUpdate":"phase_changed","phase":"{}"}}}}}}"#,
            "x".repeat(180)
        );
        for _ in 0..400 {
            writeln!(u, "{pad}").unwrap();
        }
        writeln!(
            u,
            r#"{{"params":{{"update":{{"sessionUpdate":"turn_completed"}}}}}}"#
        )
        .unwrap();
        for _ in 0..20 {
            writeln!(u, "{pad}").unwrap();
        }
        drop(u);
        let size = fs::metadata(dir.join("updates.jsonl")).unwrap().len();
        assert!(size > 64 * 1024, "fixture must exceed the 64 KiB window");
        let meta = Grok.list_meta(&dir, "sess").unwrap();
        assert_eq!(meta.turn_outcome, "completed");
        fs::remove_dir_all(&dir).unwrap();
    }
}
