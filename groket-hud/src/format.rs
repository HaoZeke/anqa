//! Display helpers for notes, status, and errors.

use serde_json::Value;

use crate::model::KindFilter;

/// Cut *s* to *max_chars* (Unicode scalar values) and mark truncation.
pub fn capped_display(s: &str, max_chars: usize) -> String {
    if max_chars == 0 {
        return String::new();
    }
    let count = s.chars().count();
    if count <= max_chars {
        return s.to_string();
    }
    let mut out: String = s.chars().take(max_chars).collect();
    out.push('…');
    out
}

/// Compact JSON (or the string itself), then apply [`capped_display`].
pub fn capped_json(value: &Value, max_chars: usize) -> String {
    let s = match value {
        Value::String(s) => s.clone(),
        other => serde_json::to_string(other).unwrap_or_default(),
    };
    capped_display(&s, max_chars)
}

/// Same labels as TUI ``ui-origin-work`` / ``ui-origin-host``.
pub fn origin_label(origin: &str) -> &'static str {
    match origin.trim().to_ascii_lowercase().as_str() {
        "host" => "Host",
        "work" | "eval" => "Eval",
        "" => "—",
        _ => "Eval",
    }
}

pub fn is_blank_status(status: &str) -> bool {
    let t = status.trim();
    t.is_empty() || t == "—" || t == "-" || t == "–"
}

/// Same short labels as :meth:`SessionMeta.list_status_label`.
pub fn list_status_label(status: &str, outcome: &str) -> String {
    if !is_blank_status(status) {
        return status.trim().to_string();
    }
    let oc = outcome
        .trim()
        .to_ascii_lowercase()
        .replace(char::is_whitespace, "_");
    match oc.as_str() {
        "ending" | "finishing" => "ending".into(),
        "awaiting_follow_up" | "awaiting" => "awaiting".into(),
        "running" | "in_progress" | "pending" => "running".into(),
        "cancelled" | "canceled" | "interrupted" | "aborted" => "cancelled".into(),
        "success" | "ok" | "completed" | "complete" => "complete".into(),
        "error" | "failed" | "failure" | "timeout" => "cancelled".into(),
        "" => "—".into(),
        _ => "complete".into(),
    }
}

pub fn status_tone(status: &str) -> &'static str {
    let s = status.to_ascii_lowercase();
    if s == "awaiting" || s.contains("await") {
        "awaiting"
    } else if s.contains("run") {
        "running"
    } else if s.contains("complete") || s == "ok" {
        "complete"
    } else if s.contains("end") {
        "ending"
    } else if s.contains("cancel")
        || s.contains("interrupt")
        || s.contains("abort")
        || s.contains("fail")
        || s == "error"
    {
        "cancelled"
    } else {
        ""
    }
}

pub fn format_note_time(iso: &str) -> String {
    let s = iso.trim();
    if s.is_empty() {
        return String::new();
    }
    if s.len() >= 16 && s.as_bytes()[4] == b'-' {
        // 2026-08-08T18:02:00 → Aug 8, 18:02
        let day: u32 = s[8..10].parse().unwrap_or(0);
        let month = match &s[5..7] {
            "01" => "Jan",
            "02" => "Feb",
            "03" => "Mar",
            "04" => "Apr",
            "05" => "May",
            "06" => "Jun",
            "07" => "Jul",
            "08" => "Aug",
            "09" => "Sep",
            "10" => "Oct",
            "11" => "Nov",
            "12" => "Dec",
            _ => &s[5..7],
        };
        let hm = if s.len() >= 16 { &s[11..16] } else { "" };
        return format!("{month} {day}, {hm}");
    }
    s.to_string()
}

pub fn note_fields_view(fields: &Value) -> (String, String, Vec<(String, String)>) {
    let obj = fields.as_object();
    let get = |k: &str| {
        obj.and_then(|m| m.get(k))
            .map(|v| match v {
                Value::String(s) => s.trim().to_string(),
                other => other.to_string(),
            })
            .unwrap_or_default()
    };
    let mut title = {
        let s = get("summary");
        if !s.is_empty() {
            s
        } else {
            let t = get("title");
            if !t.is_empty() {
                t
            } else {
                get("issue")
            }
        }
    };
    let mut body = {
        let d = get("detail");
        if !d.is_empty() {
            d
        } else {
            let b = get("body");
            if !b.is_empty() {
                b
            } else {
                let n = get("notes");
                if !n.is_empty() {
                    n
                } else {
                    get("description")
                }
            }
        }
    };
    let skip = [
        "summary",
        "title",
        "issue",
        "detail",
        "body",
        "notes",
        "description",
    ];
    let mut extras = Vec::new();
    if let Some(map) = obj {
        for (k, v) in map {
            if skip.contains(&k.as_str()) {
                continue;
            }
            let val = match v {
                Value::String(s) => s.trim().to_string(),
                other => other.to_string(),
            };
            if !val.is_empty() {
                extras.push((k.clone(), val));
            }
        }
    }
    if title.is_empty() && body.is_empty() && !extras.is_empty() {
        title = extras.remove(0).1;
    }
    if title.is_empty() && !body.is_empty() {
        if let Some(line) = body.lines().find(|l| !l.trim().is_empty()) {
            title = line.trim().chars().take(120).collect();
            if body.trim() == line.trim() {
                body.clear();
            }
        }
    }
    (title, body, extras)
}

pub fn new_note_id() -> String {
    let raw = uuid::Uuid::new_v4().simple().to_string();
    format!("n-{}", &raw[..12])
}

/// TUI timeline role (white / cyan / dim cyan / yellow / red / magenta).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventRole {
    User,
    Model,
    ModelDim,
    Session,
    Error,
    System,
    Other,
}

/// Map control ``kind`` (+ error flag) onto the same roles as the TUI type column.
pub fn event_role(kind: &str, is_error: bool) -> EventRole {
    if is_error || kind == "error" {
        return EventRole::Error;
    }
    match kind {
        "user" => EventRole::User,
        "agent" | "plan" | "tool" | "subagent" => EventRole::Model,
        "thought" | "tool_result" => EventRole::ModelDim,
        "session" | "task" => EventRole::Session,
        "system" => EventRole::System,
        _ => EventRole::Other,
    }
}

/// Collapsed cards use the one-line preview; the open card uses full ``content``.
pub fn timeline_body_text(
    preview: &str,
    content: &str,
    selected: bool,
    max_collapsed: usize,
) -> String {
    if selected {
        if !content.is_empty() {
            content.to_string()
        } else {
            preview.to_string()
        }
    } else if !preview.is_empty() {
        preview.to_string()
    } else {
        content.chars().take(max_collapsed).collect()
    }
}

/// Same cues as TUI ``panel_render.looks_like_markdown``.
pub fn looks_like_markdown(text: &str) -> bool {
    let s = text.trim_start();
    if s.is_empty() {
        return false;
    }
    if s.starts_with('#') || s.contains("```") {
        return true;
    }
    if s.starts_with("- ") || s.starts_with("* ") || s.starts_with("> ") {
        return true;
    }
    if s.contains("**") || s.contains("__") || s.contains("](http") || s.contains("](/") {
        return true;
    }
    s.contains("\n## ") || s.contains("\n# ")
}

/// Same cue as TUI ``render_detail._looks_json``.
pub fn looks_like_json(text: &str) -> bool {
    let s = text.trim();
    if s.is_empty() {
        return false;
    }
    let first = s.as_bytes()[0];
    let last = *s.as_bytes().last().unwrap_or(&0);
    (first == b'{' || first == b'[') && (last == b'}' || last == b']')
}

/// Pretty-print JSON when valid; otherwise return the original string.
pub fn pretty_json(text: &str) -> String {
    let s = text.trim();
    match serde_json::from_str::<Value>(s) {
        Ok(v) => serde_json::to_string_pretty(&v).unwrap_or_else(|_| s.to_string()),
        Err(_) => s.to_string(),
    }
}

/// TUI message bodies: soft newlines become Markdown hard breaks.
pub fn message_md_hard_breaks(body: &str) -> String {
    body.split('\n').collect::<Vec<_>>().join("  \n")
}

/// Sanitize + hard-break a chat message so iced markdown keeps lists and lines.
pub fn message_markdown_source(body: &str) -> String {
    message_md_hard_breaks(&sanitize_console_text(body))
}

/// Strip ANSI / C0 noise like TUI ``sanitize_console_text`` (display mode).
pub fn sanitize_console_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut out = String::with_capacity(text.len());
    let chars: Vec<char> = text.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == '\u{1b}' {
            i += 1;
            if i >= chars.len() {
                break;
            }
            match chars[i] {
                '[' => {
                    i += 1;
                    while i < chars.len() {
                        let ch = chars[i];
                        i += 1;
                        if ('@'..='~').contains(&ch) {
                            break;
                        }
                    }
                }
                ']' => {
                    i += 1;
                    while i < chars.len() {
                        let ch = chars[i];
                        i += 1;
                        if ch == '\u{07}' {
                            break;
                        }
                        if ch == '\u{1b}' && i < chars.len() && chars[i] == '\\' {
                            i += 1;
                            break;
                        }
                    }
                }
                _ => i += 1,
            }
            continue;
        }
        if c == '\r' {
            out.push('\n');
            i += 1;
            continue;
        }
        if c.is_control() && c != '\n' && c != '\t' {
            i += 1;
            continue;
        }
        out.push(c);
        i += 1;
    }
    let mut lines: Vec<&str> = Vec::new();
    for ln in out.split('\n') {
        let t = ln.trim_end();
        if t.is_empty() {
            if lines.last().is_some_and(|p| p.is_empty()) {
                continue;
            }
            lines.push("");
            continue;
        }
        lines.push(t);
    }
    let joined = lines.join("\n");
    let mut collapsed = String::new();
    let mut nl = 0;
    for ch in joined.chars() {
        if ch == '\n' {
            nl += 1;
            if nl <= 3 {
                collapsed.push('\n');
            }
        } else {
            nl = 0;
            collapsed.push(ch);
        }
    }
    collapsed
}

pub fn event_matches_kind(kind: &str, is_error: bool, mode: KindFilter) -> bool {
    if mode == KindFilter::All {
        return true;
    }
    let kind = kind.to_ascii_lowercase();
    match mode {
        KindFilter::All => true,
        KindFilter::Tools => kind == "tool" || kind == "tool_result",
        KindFilter::User => kind == "user",
        KindFilter::Asst => kind == "agent" || kind == "thought",
        KindFilter::Sess => {
            // TUI sess = SESSION_CHROME_TYPES → kind session | system | error.
            matches!(kind.as_str(), "system" | "session" | "error")
        }
        KindFilter::Errors => is_error || kind == "error",
    }
}

pub fn control_down_message(err: &str) -> String {
    let s = err.trim();
    let short = if s.len() > 140 {
        format!("{}…", &s[..137])
    } else {
        s.to_string()
    };
    let low = short.to_ascii_lowercase();
    if short.is_empty()
        || low.contains("no such file")
        || low.contains("connection refused")
        || low.contains("not found")
        || low.contains("os error 2")
        || low.contains("broken pipe")
        || low.contains("timed out")
        || low.contains("econnrefused")
        || low.contains("enoent")
        || low.contains("resource temporarily unavailable")
        || low.contains("os error 35")
    {
        "control socket down · run: groket serve -d".into()
    } else {
        format!("control error · {short}")
    }
}

/// One TUI-aligned tool input field (HUD inspect, not a JSON dump).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolField {
    pub id: String,
    pub label: String,
    pub value: String,
}

/// Strip Grok ``N→`` / ``N->`` prefixes from a ``read_file`` body.
pub fn strip_inline_line_prefixes(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for (i, line) in text.split('\n').enumerate() {
        if i > 0 {
            out.push('\n');
        }
        out.push_str(&strip_one_line_prefix(line));
    }
    out
}

fn strip_one_line_prefix(line: &str) -> String {
    let rest = line.trim_start();
    let indent_len = line.len() - rest.len();
    let indent = &line[..indent_len];
    let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
    if digits == 0 {
        return line.to_string();
    }
    let after = &rest[digits..];
    let stripped = if let Some(s) = after.strip_prefix('→') {
        s.strip_prefix(' ').unwrap_or(s)
    } else if let Some(s) = after.strip_prefix("->") {
        s.strip_prefix(' ').unwrap_or(s)
    } else {
        return line.to_string();
    };
    format!("{indent}{stripped}")
}

/// True when *text* looks like a numbered Grok ``read_file`` dump.
pub fn looks_like_numbered_file(text: &str) -> bool {
    let mut hits = 0;
    let mut lines = 0;
    for line in text.lines().take(12) {
        lines += 1;
        let rest = line.trim_start();
        let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
        if digits == 0 {
            continue;
        }
        let after = &rest[digits..];
        if after.starts_with('→') || after.starts_with("->") {
            hits += 1;
            if hits >= 2 {
                return true;
            }
        }
    }
    hits == 1 && lines <= 1
}

/// Display body: strip numbered prefixes on file dumps.
pub fn display_tool_output(text: &str, tool_name: &str) -> String {
    if tool_name.trim() == "read_file" || looks_like_numbered_file(text) {
        strip_inline_line_prefixes(text)
    } else {
        text.to_string()
    }
}

/// Path from ``image_gen`` / ``image_edit`` result JSON.
pub fn image_result_path(content: &str) -> String {
    let s = content.trim();
    if s.is_empty() || !(s.starts_with('{') || s.starts_with('[')) {
        return String::new();
    }
    let Ok(v) = serde_json::from_str::<Value>(s) else {
        return String::new();
    };
    v.get("path")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .unwrap_or("")
        .to_string()
}

fn json_str_field(v: &Value, key: &str) -> String {
    match v.get(key) {
        Some(Value::String(s)) => s.clone(),
        Some(other) if !other.is_null() => other.to_string(),
        _ => String::new(),
    }
}

fn take_field(map: &serde_json::Map<String, Value>, keys: &[&str]) -> String {
    for key in keys {
        let s = json_str_field(&Value::Object(map.clone()), key);
        if !s.is_empty() {
            return s;
        }
    }
    String::new()
}

/// Fields for HUD inspect from ``rawInput`` (same keys as the TUI).
pub fn tool_fields_from_raw(tool_name: &str, raw: &Value, max_chars: usize) -> Vec<ToolField> {
    let Some(obj) = raw.as_object() else {
        return Vec::new();
    };
    let mut fields = Vec::new();
    let cut = |s: String| -> String {
        if max_chars > 0 && s.chars().count() > max_chars {
            s.chars().take(max_chars).collect()
        } else {
            s
        }
    };
    let push = |fields: &mut Vec<ToolField>, id: &str, label: &str, value: String| {
        if !value.is_empty() {
            fields.push(ToolField {
                id: id.into(),
                label: label.into(),
                value: cut(value),
            });
        }
    };
    let extra_except = |skip: &[&str]| -> String {
        let mut leftover = serde_json::Map::new();
        for (k, v) in obj {
            if !skip.contains(&k.as_str()) {
                leftover.insert(k.clone(), v.clone());
            }
        }
        if leftover.is_empty() {
            return String::new();
        }
        serde_json::to_string_pretty(&Value::Object(leftover)).unwrap_or_default()
    };
    match tool_name.trim() {
        "search_replace" => {
            push(
                &mut fields,
                "file_path",
                "File",
                take_field(obj, &["file_path", "target_file"]),
            );
            push(
                &mut fields,
                "old_string",
                "old_string",
                json_str_field(raw, "old_string"),
            );
            push(
                &mut fields,
                "new_string",
                "new_string",
                json_str_field(raw, "new_string"),
            );
            push(
                &mut fields,
                "extra",
                "extra",
                extra_except(&["file_path", "target_file", "old_string", "new_string"]),
            );
        }
        "run_terminal_command" => {
            push(
                &mut fields,
                "command",
                "command",
                json_str_field(raw, "command"),
            );
            push(&mut fields, "extra", "extra", extra_except(&["command"]));
        }
        "read_file" => {
            push(
                &mut fields,
                "target_file",
                "target_file",
                take_field(obj, &["target_file", "file_path"]),
            );
            push(
                &mut fields,
                "extra",
                "extra",
                extra_except(&["target_file", "file_path"]),
            );
        }
        "list_dir" => {
            push(
                &mut fields,
                "target_directory",
                "target_directory",
                take_field(obj, &["target_directory", "path"]),
            );
            push(
                &mut fields,
                "extra",
                "extra",
                extra_except(&["target_directory", "path"]),
            );
        }
        "grep" => {
            push(
                &mut fields,
                "pattern",
                "pattern",
                json_str_field(raw, "pattern"),
            );
            push(&mut fields, "extra", "extra", extra_except(&["pattern"]));
        }
        "web_search" => {
            push(&mut fields, "query", "query", json_str_field(raw, "query"));
            push(&mut fields, "url", "url", json_str_field(raw, "url"));
            push(
                &mut fields,
                "extra",
                "extra",
                extra_except(&["query", "url", "variant", "backend"]),
            );
        }
        _ => {
            for key in [
                "command",
                "query",
                "pattern",
                "target_file",
                "file_path",
                "path",
                "prompt",
            ] {
                push(&mut fields, key, key, json_str_field(raw, key));
            }
        }
    }
    fields
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn soft_control_down_copy() {
        assert_eq!(
            control_down_message("connection refused"),
            "control socket down · run: groket serve -d"
        );
    }

    #[test]
    fn list_status_prefers_status_then_outcome() {
        assert_eq!(list_status_label("complete", ""), "complete");
        assert_eq!(list_status_label("—", "completed"), "complete");
        assert_eq!(list_status_label("", "awaiting_follow_up"), "awaiting");
        assert_eq!(list_status_label("", "cancelled"), "cancelled");
        assert_eq!(list_status_label("", "running"), "running");
        assert_eq!(list_status_label("", ""), "—");
        assert_eq!(status_tone("cancelled"), "cancelled");
        assert_eq!(status_tone("complete"), "complete");
        assert!(is_blank_status("—"));
        assert!(!is_blank_status("complete"));
    }

    #[test]
    fn sess_filter_matches_tui_session_chrome_kinds() {
        assert!(event_matches_kind("session", false, KindFilter::Sess));
        assert!(event_matches_kind("system", false, KindFilter::Sess));
        assert!(event_matches_kind("error", false, KindFilter::Sess));
        assert!(!event_matches_kind("plan", false, KindFilter::Sess));
        assert!(!event_matches_kind("subagent", false, KindFilter::Sess));
        assert!(!event_matches_kind("agent", false, KindFilter::Sess));
        assert!(event_matches_kind("agent", false, KindFilter::Asst));
        assert!(event_matches_kind("thought", false, KindFilter::Asst));
    }

    #[test]
    fn event_role_matches_tui_type_column() {
        assert_eq!(event_role("user", false), EventRole::User);
        assert_eq!(event_role("agent", false), EventRole::Model);
        assert_eq!(event_role("tool", false), EventRole::Model);
        assert_eq!(event_role("plan", false), EventRole::Model);
        assert_eq!(event_role("subagent", false), EventRole::Model);
        assert_eq!(event_role("thought", false), EventRole::ModelDim);
        assert_eq!(event_role("tool_result", false), EventRole::ModelDim);
        assert_eq!(event_role("session", false), EventRole::Session);
        assert_eq!(event_role("task", false), EventRole::Session);
        assert_eq!(event_role("system", false), EventRole::System);
        assert_eq!(event_role("agent", true), EventRole::Error);
        assert_eq!(event_role("error", false), EventRole::Error);
        assert_eq!(event_role("other", false), EventRole::Other);
    }

    #[test]
    fn timeline_open_card_uses_full_content_not_preview_line() {
        let preview = "first line only";
        let content = "first line only\nrest of the tool output\nand more";
        assert_eq!(
            timeline_body_text(preview, content, false, 80),
            "first line only"
        );
        assert_eq!(timeline_body_text(preview, content, true, 80), content);
        assert_eq!(timeline_body_text("", "abcdef", false, 3), "abc");
    }

    #[test]
    fn capped_display_and_json() {
        assert_eq!(capped_display("abcd", 10), "abcd");
        assert_eq!(capped_display("abcdef", 3), "abc…");
        assert_eq!(capped_display("x", 0), "");
        let v = serde_json::json!({"a": "bbbb"});
        let s = capped_json(&v, 8);
        assert!(s.chars().count() <= 9);
        assert!(s.ends_with('…') || s.len() <= 8);
    }

    #[test]
    fn origin_label_matches_tui() {
        assert_eq!(origin_label("host"), "Host");
        assert_eq!(origin_label("work"), "Eval");
        assert_eq!(origin_label("eval"), "Eval");
        assert_eq!(origin_label(""), "—");
    }

    #[test]
    fn looks_like_markdown_matches_tui_cues() {
        assert!(looks_like_markdown("# heading"));
        assert!(looks_like_markdown("- item\n- two"));
        assert!(looks_like_markdown("see **bold**"));
        assert!(looks_like_markdown("```\ncode\n```"));
        assert!(!looks_like_markdown("plain sentence"));
        assert!(!looks_like_markdown(""));
    }

    #[test]
    fn looks_like_json_and_pretty() {
        assert!(looks_like_json("{\"a\":1}"));
        assert!(looks_like_json(" [1, 2] "));
        assert!(!looks_like_json("not json"));
        let pretty = pretty_json("{\"a\":1}");
        assert!(pretty.contains('\n'));
        assert!(pretty.contains("\"a\""));
    }

    #[test]
    fn message_md_hard_breaks_preserves_lines() {
        assert_eq!(message_md_hard_breaks("a\nb"), "a  \nb");
    }

    #[test]
    fn message_markdown_source_keeps_numbered_lists() {
        let src = message_markdown_source("Intro\n\n1. first\n2. second\n\n**bold**");
        assert!(src.contains("1. first"));
        assert!(src.contains("2. second"));
        assert!(src.contains("**bold**"));
        assert!(src.contains("  \n") || src.contains("Intro"));
    }

    #[test]
    fn strip_inline_line_prefixes_drops_grok_arrows() {
        let raw = "1→from pathlib import Path\n10→x = 1\n";
        let out = strip_inline_line_prefixes(raw);
        assert!(!out.contains('→'));
        assert!(out.starts_with("from pathlib"));
        assert!(out.contains("x = 1"));
        assert_eq!(strip_inline_line_prefixes("plain"), "plain");
    }

    #[test]
    fn display_tool_output_strips_read_file() {
        let raw = "1→fn main() {}\n2→// hi\n";
        let out = display_tool_output(raw, "read_file");
        assert_eq!(out, "fn main() {}\n// hi\n");
        assert!(looks_like_numbered_file(raw));
    }

    #[test]
    fn image_result_path_reads_json() {
        let body = r#"{"path":"/tmp/img.jpg","filename":"img.jpg","message":"saved"}"#;
        assert_eq!(image_result_path(body), "/tmp/img.jpg");
        assert_eq!(image_result_path("not json"), "");
    }

    #[test]
    fn tool_fields_search_replace_and_command_not_one_json_bag() {
        let raw = serde_json::json!({
            "file_path": "a.py",
            "old_string": "aaa",
            "new_string": "bbb extra long would still be a field",
        });
        let fields = tool_fields_from_raw("search_replace", &raw, 8_000);
        let ids: Vec<&str> = fields.iter().map(|f| f.id.as_str()).collect();
        assert_eq!(ids, ["file_path", "old_string", "new_string"]);
        assert_eq!(fields[1].value, "aaa");
        let cmd = tool_fields_from_raw(
            "run_terminal_command",
            &serde_json::json!({"command": "ls -la", "timeout": 30}),
            100,
        );
        assert_eq!(cmd[0].id, "command");
        assert_eq!(cmd[0].value, "ls -la");
        assert!(cmd.iter().all(|f| f.id != "json"));
        let ws = tool_fields_from_raw(
            "web_search",
            &serde_json::json!({
                "variant": "WebSearch",
                "backend": true,
                "query": "xAI logo",
                "url": "https://x.ai/"
            }),
            200,
        );
        let ids: Vec<&str> = ws.iter().map(|f| f.id.as_str()).collect();
        assert_eq!(ids, ["query", "url"]);
        assert_eq!(ws[0].value, "xAI logo");
    }

    #[test]
    fn sanitize_strips_csi_and_cr() {
        let s = sanitize_console_text("ok\x1b[31mred\x1b[0m\r\nnext");
        assert!(!s.contains('\u{1b}'));
        assert!(s.contains("ok"));
        assert!(s.contains("red"));
        assert!(s.contains("next"));
    }
}
