//! Compact turn, stat, and job-bookend walk over a cached timeline.

use crate::event::{Event, EventType};
use std::collections::HashSet;

const USER_PREVIEW: usize = 320;
const ASSISTANT_PREVIEW: usize = 400;
const YEAR_SECS: i64 = 86_400 * 365;

const CHROME_TAGS: &[&str] = &[
    "system-reminder",
    "system_reminder",
    "timestamp",
    "monitor-event",
    "user-prompt-submit-hook",
    "session_context",
    "hook_context",
];
const TOOL_TAGS: &[&str] = &[
    "workspace_result",
    "task-id",
    "task-type",
    "output-file",
    "summary",
    "status",
];
const PREAMBLE_TAGS: &[&str] = &[
    "user_info",
    "git_status",
    "tool_calling",
    "formatting",
    "background_tasks",
    "action_safety",
    "output_efficiency",
    "user_guide",
    "inline_line_numbers",
    "project_instructions_spec",
    "making_code_changes",
    "mcp_tools",
    "system_information",
    "tone_and_style",
    "skill_information",
    "runtime_context",
    "operator_instructions",
    "non_negotiables",
    "code_creation",
    "communication",
    "think_before_coding",
    "simplicity",
    "surgical_changes",
    "review_constraints",
];

/// One event-type or tool count.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CountRow {
    pub id: String,
    pub count: u32,
}

/// One compact turn for ``session/overview``.
#[derive(Clone, Debug, PartialEq)]
pub struct Turn {
    pub turn_index: i32,
    pub turn_number: Option<i32>,
    pub prompt_index: Option<i32>,
    pub outcome: String,
    pub open: bool,
    pub label: String,
    pub summary: String,
    pub user_event_index: Option<u32>,
    pub assistant_summary: String,
    pub assistant_event_index: Option<u32>,
    pub event_count: u32,
    pub tool_call_count: u32,
    pub tool_error_count: u32,
    pub user_count: u32,
    pub assistant_count: u32,
    pub error_event_count: u32,
    pub first_index: Option<u32>,
    pub last_index: Option<u32>,
    pub duration_seconds: Option<f64>,
}

/// Compact overview of a cached event slice.
#[derive(Clone, Debug)]
pub struct Overview {
    pub num_events: usize,
    pub turns: Vec<Turn>,
    pub event_types: Vec<CountRow>,
    pub tools: Vec<CountRow>,
    pub subagent_count: u32,
    pub bookends: Vec<Event>,
}

impl Overview {
    /// Walk *events* once for turns, stats, and job bookends.
    #[must_use]
    pub fn from_events(events: &[Event]) -> Self {
        let mut event_types = Vec::new();
        let mut tools = Vec::new();
        let mut bookends = Vec::new();
        let mut children = HashSet::new();
        let mut spawn_anon = 0u32;
        for ev in events {
            bump_count(&mut event_types, ev.event_type.as_str());
            if ev.event_type.is_tool_call() && !ev.tool_name.trim().is_empty() {
                bump_count(&mut tools, ev.tool_name.trim());
            }
            if ev.is_overview_bookend() {
                bookends.push(ev.clone());
            }
            if ev.event_type.is_subagent() {
                if ev.child_session_id.is_empty() {
                    if matches!(ev.event_type, EventType::SubagentSpawned) {
                        spawn_anon += 1;
                    }
                } else {
                    children.insert(ev.child_session_id.clone());
                }
            }
        }
        sort_counts(&mut event_types);
        sort_counts(&mut tools);
        Self {
            num_events: events.len(),
            turns: Turn::segment(events),
            event_types,
            tools,
            subagent_count: children.len() as u32 + spawn_anon,
            bookends,
        }
    }
}

impl Turn {
    /// Segment *events* the same way the Python turn walk does.
    #[must_use]
    pub fn segment(events: &[Event]) -> Vec<Self> {
        let body: Vec<usize> = events
            .iter()
            .enumerate()
            .filter(|(_, ev)| !ev.event_type.is_system())
            .map(|(i, _)| i)
            .collect();
        if body.is_empty() {
            return Vec::new();
        }
        let has_markers = body
            .iter()
            .any(|&i| events[i].is_turn_started() || events[i].is_turn_ended());
        let slices = if has_markers {
            attach_startless(stamp_ids(split_markers(events, &body)), events)
        } else {
            stamp_ids(vec![Slice::open(body, None)])
        };
        slices
            .into_iter()
            .enumerate()
            .map(|(i, slice)| slice.into_turn(events, i as i32))
            .collect()
    }
}

struct Slice {
    indexes: Vec<usize>,
    turn_number: Option<i32>,
    outcome: String,
    open: bool,
}

impl Slice {
    fn open(indexes: Vec<usize>, turn_number: Option<i32>) -> Self {
        Self {
            indexes,
            turn_number,
            outcome: String::new(),
            open: true,
        }
    }

    fn closed(indexes: Vec<usize>, turn_number: Option<i32>, outcome: String) -> Self {
        Self {
            indexes,
            turn_number,
            outcome,
            open: false,
        }
    }

    fn has_start(&self, events: &[Event]) -> bool {
        self.indexes.iter().any(|&i| events[i].is_turn_started())
    }

    fn has_end(&self, events: &[Event]) -> bool {
        self.indexes.iter().any(|&i| events[i].is_turn_ended())
    }

    fn closed_host(&self, events: &[Event]) -> bool {
        self.has_end(events) || (!self.open && !self.outcome.is_empty())
    }

    fn into_turn(self, events: &[Event], turn_index: i32) -> Turn {
        let mut tool_call_count = 0u32;
        let mut tool_error_count = 0u32;
        let mut user_count = 0u32;
        let mut assistant_count = 0u32;
        let mut error_event_count = 0u32;
        let mut summary = String::new();
        let mut user_event_index = None;
        let mut assistant_summary = String::new();
        let mut assistant_event_index = None;
        let mut prompt_index = None;
        let mut prompt_fallback = None;
        let mut ts: Vec<i64> = Vec::new();
        for &i in &self.indexes {
            let ev = &events[i];
            if ev.event_type.is_tool_call() {
                tool_call_count += 1;
                if ev.is_error {
                    tool_error_count += 1;
                }
            }
            if ev.event_type.is_user() {
                user_count += 1;
                if prompt_fallback.is_none() {
                    prompt_fallback = ev.prompt_index;
                }
            }
            if ev.event_type.is_agent() {
                assistant_count += 1;
                let text = unwrap_display(&ev.content);
                if !text.is_empty() {
                    assistant_summary = clip(&text, ASSISTANT_PREVIEW);
                    assistant_event_index = Some(ev.index);
                }
            }
            if ev.is_error || ev.event_type.is_error_kind() {
                error_event_count += 1;
            }
            if ev.event_type.is_user() {
                let preview = operator_preview(&ev.content, USER_PREVIEW);
                if !preview.is_empty() && summary.is_empty() {
                    summary = preview;
                    user_event_index = Some(ev.index);
                    if ev.prompt_index.is_some() {
                        prompt_index = ev.prompt_index;
                    }
                }
            }
            if let Some(t) = ev.timestamp {
                ts.push(t);
            }
        }
        if prompt_index.is_none() {
            prompt_index = prompt_fallback;
        }
        let first_index = self.indexes.first().map(|&i| events[i].index);
        let last_index = self.indexes.last().map(|&i| events[i].index);
        let label = turn_label(self.turn_number, self.open, &self.outcome);
        Turn {
            turn_index,
            turn_number: self.turn_number,
            prompt_index,
            outcome: self.outcome,
            open: self.open,
            label,
            summary,
            user_event_index,
            assistant_summary,
            assistant_event_index,
            event_count: self.indexes.len() as u32,
            tool_call_count,
            tool_error_count,
            user_count,
            assistant_count,
            error_event_count,
            first_index,
            last_index,
            duration_seconds: duration_secs(&ts),
        }
    }
}

fn split_markers(events: &[Event], body: &[usize]) -> Vec<Slice> {
    let mut segments: Vec<Slice> = Vec::new();
    let mut current: Option<Slice> = None;
    for &i in body {
        let ev = &events[i];
        if ev.is_turn_started() {
            start_turn(events, ev, i, &mut current, &mut segments);
            continue;
        }
        if ev.is_turn_ended() {
            end_turn(events, ev, i, &mut current, &mut segments);
            continue;
        }
        push_body(ev, i, &mut current, &mut segments);
    }
    if let Some(cur) = current {
        if !cur.indexes.is_empty() {
            segments.push(cur);
        }
    }
    segments
}

fn start_turn(
    events: &[Event],
    ev: &Event,
    i: usize,
    current: &mut Option<Slice>,
    segments: &mut Vec<Slice>,
) {
    let tn = ev.parsed_turn_number();
    if let Some(cur) = current.as_mut() {
        if !cur.indexes.is_empty() && cur.open && !cur.has_start(events) {
            cur.indexes.push(i);
            if tn.is_some() {
                cur.turn_number = tn;
            }
            return;
        }
        if !cur.indexes.is_empty() {
            if cur.open && cur.outcome.is_empty() {
                cur.outcome = "unknown".into();
            }
            cur.open = false;
            segments.push(current.take().expect("current held a slice"));
        }
    }
    let mut next = Slice::open(vec![i], tn);
    next.open = true;
    *current = Some(next);
}

fn end_turn(
    events: &[Event],
    ev: &Event,
    i: usize,
    current: &mut Option<Slice>,
    segments: &mut Vec<Slice>,
) {
    let outcome = ev.outcome();
    if let Some(cur) = current.as_mut() {
        if cur.open
            && !cur.indexes.is_empty()
            && ev.is_events_jsonl_turn_end()
            && !cur.has_start(events)
            && !segments.is_empty()
        {
            let prev = segments.last_mut().expect("segments non-empty");
            prev.indexes.push(i);
            if !outcome.is_empty() {
                prev.outcome = outcome;
            }
            prev.open = false;
            return;
        }
        cur.indexes.push(i);
        if !outcome.is_empty() {
            cur.outcome = outcome;
        }
        cur.open = false;
        segments.push(current.take().expect("current held a slice"));
        return;
    }
    if let Some(prev) = segments.last_mut() {
        prev.indexes.push(i);
        if !outcome.is_empty() {
            prev.outcome = outcome;
        }
        prev.open = false;
        return;
    }
    segments.push(Slice::closed(vec![i], None, outcome));
}

fn push_body(ev: &Event, i: usize, current: &mut Option<Slice>, segments: &mut [Slice]) {
    if let Some(cur) = current.as_mut() {
        cur.indexes.push(i);
        return;
    }
    if let Some(prev) = segments.last_mut() {
        if ev.event_type.is_user() {
            if is_chrome(&ev.content) {
                prev.indexes.push(i);
            } else {
                *current = Some(Slice::open(vec![i], None));
            }
        } else {
            prev.indexes.push(i);
        }
        return;
    }
    *current = Some(Slice::open(vec![i], None));
}

fn stamp_ids(mut segments: Vec<Slice>) -> Vec<Slice> {
    let has_trace = segments.iter().any(|s| s.turn_number.is_some());
    if !has_trace {
        for (i, seg) in segments.iter_mut().enumerate() {
            seg.turn_number = Some(i as i32);
        }
    }
    segments
}

fn attach_startless(segments: Vec<Slice>, events: &[Event]) -> Vec<Slice> {
    if segments.is_empty() {
        return segments;
    }
    let mut out: Vec<Slice> = Vec::new();
    let mut pending: Vec<Slice> = Vec::new();
    let mut seen_numbered = false;
    for seg in segments {
        if seg.turn_number.is_none() {
            if seen_numbered || !seg.closed_host(events) {
                pending.push(seg);
            } else {
                out.push(seg);
            }
            continue;
        }
        if !pending.is_empty() {
            let mut joined = pending
                .drain(..)
                .flat_map(|p| p.indexes)
                .collect::<Vec<_>>();
            joined.extend(seg.indexes.iter().copied());
            let mut merged = seg;
            merged.indexes = joined;
            out.push(merged);
        } else {
            out.push(seg);
        }
        seen_numbered = true;
    }
    if !pending.is_empty() {
        if let Some(last) = out.last_mut() {
            for p in pending {
                last.indexes.extend(p.indexes);
            }
        } else {
            out.extend(pending);
        }
    }
    number_host_prefix(&mut out);
    out
}

fn number_host_prefix(segments: &mut [Slice]) {
    let mut used: HashSet<i32> = segments.iter().filter_map(|s| s.turn_number).collect();
    let mut n = 0i32;
    for seg in segments.iter_mut() {
        if seg.turn_number.is_some() {
            continue;
        }
        while used.contains(&n) {
            n += 1;
        }
        seg.turn_number = Some(n);
        used.insert(n);
        n += 1;
    }
}

fn turn_label(turn_number: Option<i32>, open: bool, outcome: &str) -> String {
    let head = match turn_number {
        Some(n) => format!("turn {n}"),
        None => "unnumbered".into(),
    };
    if open {
        return format!("{head} (open)");
    }
    if !outcome.is_empty() {
        return format!("{head} ({outcome})");
    }
    head
}

fn duration_secs(ts: &[i64]) -> Option<f64> {
    if ts.len() < 2 {
        return None;
    }
    let delta = ts.iter().copied().max()? - ts.iter().copied().min()?;
    if delta > YEAR_SECS {
        return Some(delta as f64 / 1000.0);
    }
    Some(delta as f64)
}

fn bump_count(rows: &mut Vec<CountRow>, id: &str) {
    if id.is_empty() {
        return;
    }
    if let Some(row) = rows.iter_mut().find(|r| r.id == id) {
        row.count += 1;
        return;
    }
    rows.push(CountRow {
        id: id.to_string(),
        count: 1,
    });
}

fn sort_counts(rows: &mut [CountRow]) {
    rows.sort_by_key(|row| std::cmp::Reverse(row.count));
}

fn clip(text: &str, max_chars: usize) -> String {
    if max_chars == 0 || text.len() <= max_chars {
        return text.to_string();
    }
    let mut end = max_chars.saturating_sub(1);
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}…", &text[..end])
}

fn user_query(content: &str) -> Option<String> {
    let low = content.to_ascii_lowercase();
    let open = "<user_query>";
    let i = low.find(open)?;
    let from = i + open.len();
    let j = low.get(from..)?.find("</user_query>")?;
    let body = content.get(from..from + j)?.trim();
    if body.is_empty() {
        return None;
    }
    Some(body.to_string())
}

fn outer_tag(content: &str) -> Option<(String, &str)> {
    let t = content.trim();
    if !t.starts_with('<') {
        return None;
    }
    let gt = t.find('>')?;
    let name = t
        .get(1..gt)?
        .split_whitespace()
        .next()?
        .to_ascii_lowercase();
    if name.is_empty() {
        return None;
    }
    let close = format!("</{name}>");
    if !t.to_ascii_lowercase().ends_with(&close) {
        return None;
    }
    let body_end = t.len().checked_sub(close.len())?;
    let body = t.get(gt + 1..body_end)?.trim();
    Some((name, body))
}

fn dialect_tag(name: &str) -> bool {
    let mut chars = name.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if !first.is_ascii_lowercase() {
        return false;
    }
    let mut saw_sep = false;
    for c in chars {
        if c == '_' || c == '-' {
            saw_sep = true;
            continue;
        }
        if !c.is_ascii_lowercase() && !c.is_ascii_digit() {
            return false;
        }
    }
    saw_sep
}

fn known_chrome(name: &str) -> bool {
    CHROME_TAGS.contains(&name) || TOOL_TAGS.contains(&name) || PREAMBLE_TAGS.contains(&name)
}

fn is_chrome(content: &str) -> bool {
    let c = content.trim();
    if c.is_empty() {
        return false;
    }
    if let Some((name, _)) = outer_tag(c) {
        if name == "user_query" {
            return false;
        }
        if known_chrome(&name) || dialect_tag(&name) {
            return true;
        }
    }
    let cl = c.to_ascii_lowercase();
    cl.starts_with("<system-reminder>")
        || cl.starts_with("<system_reminder>")
        || cl.contains("background task")
        || cl.contains("task-completed-call-")
}

fn operator_preview(content: &str, max_chars: usize) -> String {
    let raw = content.trim();
    if raw.is_empty() {
        return String::new();
    }
    let text = if let Some(uq) = user_query(raw) {
        uq
    } else if is_chrome(raw) {
        return String::new();
    } else {
        raw.to_string()
    };
    clip(&text, max_chars)
}

fn unwrap_display(content: &str) -> String {
    let raw = content.trim();
    if raw.is_empty() {
        return String::new();
    }
    if let Some(uq) = user_query(raw) {
        return uq;
    }
    if let Some((name, body)) = outer_tag(raw) {
        if name != "user_query" && (known_chrome(&name) || dialect_tag(&name)) {
            return body.to_string();
        }
    }
    raw.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(index: u32, kind: EventType, content: &str, ts: i64) -> Event {
        let mut event = Event::new(kind).with_content(content).with_ts(Some(ts));
        event.index = index;
        event
    }

    #[test]
    fn host_only_session_is_one_open_turn() {
        let mut tool = ev(2, EventType::ToolCall, "read_file", 1002);
        tool.tool_name = "read_file".into();
        let evs = vec![
            {
                let mut user = ev(0, EventType::UserMessageChunk, "hello user", 1000);
                user.prompt_index = Some(2);
                user
            },
            ev(1, EventType::AgentMessageChunk, "hello agent", 1001),
            tool,
        ];
        let ov = Overview::from_events(&evs);
        assert_eq!(ov.num_events, 3);
        assert_eq!(ov.turns.len(), 1);
        let t0 = &ov.turns[0];
        assert_eq!(t0.summary, "hello user");
        assert_eq!(t0.assistant_summary, "hello agent");
        assert_eq!(t0.event_count, 3);
        assert_eq!(t0.tool_call_count, 1);
        assert_eq!(t0.user_event_index, Some(0));
        assert_eq!(t0.assistant_event_index, Some(1));
        assert_eq!(t0.prompt_index, Some(2));
        assert!(t0.open);
        assert_eq!(t0.duration_seconds, Some(2.0));
        assert_eq!(ov.tools[0].id, "read_file");
        assert_eq!(ov.tools[0].count, 1);
        assert!(ov.bookends.is_empty());
    }

    #[test]
    fn job_bookends_are_kept() {
        let mut job = ev(0, EventType::TaskBackgrounded, "", 10);
        job.raw = r#"{"task_id":"job-ov","command":"watch"}"#.into();
        let ov = Overview::from_events(&[job]);
        assert_eq!(ov.bookends.len(), 1);
        assert!(matches!(
            ov.bookends[0].event_type,
            EventType::TaskBackgrounded
        ));
    }
}
