//! Catalog query language. Tokens come from the published control contract
//! (`desktop/assets/catalog-query.json`, same `catalogQuery` as the schema).

use std::path::Path;
use std::sync::OnceLock;

use regex::Regex;
use serde::Deserialize;

use crate::model::KindFilter;
use crate::wire::{SessionListItem, TimelineEvent, TurnRow};

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct QueryScheme {
    compare: Vec<String>,
    #[serde(default)]
    operators: Vec<String>,
    #[serde(default)]
    counts: Vec<QueryCountSpec>,
    tokens: Vec<QueryTokenSpec>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct QueryCountSpec {
    flag: String,
    count: String,
    field: String,
}

/// One highlighted slice of a catalog query (byte offsets).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QuerySpan {
    pub start: usize,
    pub end: usize,
    pub kind: QuerySpanKind,
}

/// Schema token role for catalog search color.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QuerySpanKind {
    Field,
    Value,
    Unknown,
    Operator,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct QueryTokenSpec {
    name: String,
    #[serde(default)]
    role: String,
    #[serde(default)]
    values: Vec<String>,
    #[serde(default)]
    compare: bool,
    #[serde(default)]
    #[allow(dead_code)]
    count_fields: std::collections::BTreeMap<String, String>,
}

/// One catalog-search help row (label plus wrapped values or role).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QueryHelpRow {
    pub label: String,
    pub body: String,
}

/// Same token list as TUI `?` / the published `catalogQuery` schema.
pub fn catalog_query_help_rows() -> Vec<QueryHelpRow> {
    let scheme = scheme();
    let mut rows = Vec::new();
    let mut compare: Vec<&str> = Vec::new();
    for token in &scheme.tokens {
        if !token.values.is_empty() {
            rows.push(QueryHelpRow {
                label: format!("{}:", token.name),
                body: token.values.join(", "),
            });
            if !scheme.counts.is_empty() && token.name == "has" {
                let pairs = scheme
                    .counts
                    .iter()
                    .map(|c| format!("has:{} {}:>=N", c.flag, c.count))
                    .collect::<Vec<_>>()
                    .join(", ");
                rows.push(QueryHelpRow {
                    label: String::new(),
                    body: pairs,
                });
            }
        } else if token.compare {
            compare.push(token.name.as_str());
        } else {
            let role = token.role.trim_end_matches('.');
            rows.push(QueryHelpRow {
                label: format!("{}:", token.name),
                body: role.to_string(),
            });
        }
    }
    if !compare.is_empty() {
        let names = compare
            .iter()
            .map(|n| format!("{n}:"))
            .collect::<Vec<_>>()
            .join(" ");
        rows.push(QueryHelpRow {
            label: names,
            body: scheme.compare.join("  "),
        });
    }
    let mut ops = scheme.operators.clone();
    ops.extend(["(".into(), ")".into()]);
    rows.push(QueryHelpRow {
        label: ops.join("  "),
        body: String::new(),
    });
    rows
}

fn scheme() -> &'static QueryScheme {
    static CELL: OnceLock<QueryScheme> = OnceLock::new();
    CELL.get_or_init(|| {
        serde_json::from_str(include_str!("../assets/catalog-query.json"))
            .expect("catalog-query.json")
    })
}

fn field_names() -> impl Iterator<Item = &'static str> {
    scheme().tokens.iter().map(|t| t.name.as_str())
}

fn token_values(name: &str) -> &[String] {
    scheme()
        .tokens
        .iter()
        .find(|t| t.name == name)
        .map(|t| t.values.as_slice())
        .unwrap_or(&[])
}

fn count_specs() -> &'static [QueryCountSpec] {
    scheme().counts.as_slice()
}

fn count_wire(name: &str) -> Option<&'static str> {
    count_specs()
        .iter()
        .find(|c| c.count.eq_ignore_ascii_case(name) || c.flag.eq_ignore_ascii_case(name))
        .map(|c| c.field.as_str())
}

fn known_has_flag(name: &str) -> bool {
    token_values("has")
        .iter()
        .any(|item| item.eq_ignore_ascii_case(name))
}

/// Presence flags for ``has:`` (row attributes, not the token list).
type HasFlag = fn(&SessionListItem) -> bool;
const HAS_FLAGS: &[(&str, HasFlag)] = &[
    ("workflow", |r| r.has_workflows),
    ("note", |r| r.has_notes),
    ("goal", |r| r.has_goals),
    ("subagent", |r| r.has_subagents),
    ("job", |r| r.has_jobs),
    ("schedule", |r| r.has_schedules),
    ("plan", |r| r.has_plan),
    ("failure", |r| r.has_failures),
    ("diff", |r| r.has_diff),
    ("compaction", |r| r.has_compaction),
    ("doom", |r| r.has_doom),
];

const SKIP_WORDS: &[&str] = &["and", "or", "not", "(", ")", "((", "))"];

#[derive(Debug, Clone, PartialEq)]
enum Node {
    And(Vec<Node>),
    Or(Vec<Node>),
    Not(Box<Node>),
    Pred { field: String, value: String },
    Text(String),
}

pub fn finished_prefix(query: &str) -> String {
    let mut text = query.trim_end().to_string();
    let lower = text.to_ascii_lowercase();
    for field in field_names() {
        let needle = format!("{field}:");
        if lower.ends_with(&needle)
            && (lower.len() == needle.len()
                || lower
                    .as_bytes()
                    .get(lower.len() - needle.len() - 1)
                    .is_some_and(|b| b.is_ascii_whitespace()))
        {
            text.truncate(text.len() - needle.len());
            text = text.trim_end().to_string();
            break;
        }
    }
    let low = text.to_ascii_lowercase();
    for op in ["and", "or", "not"] {
        if low == op
            || (low.ends_with(op)
                && low
                    .as_bytes()
                    .get(low.len() - op.len() - 1)
                    .is_some_and(|b| b.is_ascii_whitespace()))
        {
            text.truncate(text.len() - op.len());
            return text.trim_end().to_string();
        }
    }
    text
}

pub fn query_has_tokens(query: &str) -> bool {
    let text = finished_prefix(query).to_ascii_lowercase();
    field_names().any(|f| {
        text.split(|c: char| c.is_ascii_whitespace() || c == '(' || c == ')')
            .any(|tok| tok.starts_with(&format!("{f}:")))
    })
}

/// Paint offsets for known fields, values, and operators.
///
/// Live box color uses this scanner because the parse tree has no source
/// offsets. Matching still goes through [`row_matches`].
pub fn highlight_query_spans(query: &str) -> Vec<QuerySpan> {
    let mut spans = Vec::new();
    for caps in highlight_re().captures_iter(query) {
        let start = caps.get(0).map_or(0, |m| m.start());
        if start > 0 {
            let prev = query.as_bytes()[start - 1];
            if !prev.is_ascii_whitespace() && prev != b'(' {
                continue;
            }
        }
        if let Some(op) = caps.name("operator") {
            spans.push(QuerySpan {
                start: op.start(),
                end: op.end(),
                kind: QuerySpanKind::Operator,
            });
            continue;
        }
        if let Some(prohibit) = caps.name("prohibit") {
            spans.push(QuerySpan {
                start: prohibit.start(),
                end: prohibit.end(),
                kind: QuerySpanKind::Operator,
            });
        }
        let Some(field) = caps.name("field") else {
            continue;
        };
        spans.push(QuerySpan {
            start: field.start(),
            end: field.end() + 1,
            kind: QuerySpanKind::Field,
        });
        let Some(value) = caps.name("value") else {
            continue;
        };
        if value.as_str().is_empty() {
            continue;
        }
        let raw = value.as_str();
        let quoted = quoted_value(raw);
        let field_key = field.as_str().to_ascii_lowercase();
        let closed = token_values(&field_key);
        let mut value_start = value.start();
        let mut value_end = value.end();
        if !quoted && closed.is_empty() {
            value_end = extend_open_value(query, value_end);
        }
        if !quoted {
            (value_start, value_end) = trim_value_span(query, value_start, value_end);
        }
        if value_start >= value_end {
            continue;
        }
        let inner = if quoted {
            &query[value_start + 1..value_end - 1]
        } else {
            &query[value_start..value_end]
        };
        if field_key == "has" && !quoted {
            spans.extend(has_value_spans(value_start, value_end, inner, closed));
            continue;
        }
        spans.push(QuerySpan {
            start: value_start,
            end: value_end,
            kind: value_kind(&field_key, inner, closed),
        });
    }
    spans
}

fn has_value_spans(start: usize, end: usize, inner: &str, closed: &[String]) -> Vec<QuerySpan> {
    let (name, cmp) = split_has_value(inner);
    let known = closed.iter().any(|item| item.eq_ignore_ascii_case(&name));
    if !cmp.is_empty() || !known {
        return vec![QuerySpan {
            start,
            end,
            kind: QuerySpanKind::Unknown,
        }];
    }
    vec![QuerySpan {
        start,
        end,
        kind: QuerySpanKind::Value,
    }]
}

fn split_has_value(raw: &str) -> (String, String) {
    match raw.split_once(':') {
        Some((name, rest)) => (name.to_ascii_lowercase(), rest.to_string()),
        None => (raw.to_ascii_lowercase(), String::new()),
    }
}

fn value_kind(field: &str, inner: &str, closed: &[String]) -> QuerySpanKind {
    if closed.is_empty() || closed.iter().any(|item| item.eq_ignore_ascii_case(inner)) {
        return QuerySpanKind::Value;
    }
    if field == "is"
        && matches!(
            inner.to_ascii_lowercase().as_str(),
            "cancelled" | "canceled"
        )
    {
        return QuerySpanKind::Value;
    }
    QuerySpanKind::Unknown
}

fn quoted_value(raw: &str) -> bool {
    raw.len() >= 2 && raw.starts_with('"') && raw.ends_with('"')
}

fn is_field_token(word: &str) -> bool {
    word.split_once(':')
        .is_some_and(|(head, _)| field_names().any(|name| name.eq_ignore_ascii_case(head)))
}

fn extend_open_value(query: &str, mut end: usize) -> usize {
    let bytes = query.as_bytes();
    while end < bytes.len() {
        if bytes[end] == b')' {
            return end;
        }
        let mut i = end;
        while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
            i += 1;
        }
        if i == end || i == bytes.len() {
            return end;
        }
        let mut j = i;
        while j < bytes.len() && !matches!(bytes[j], b' ' | b'\t' | b')') {
            j += 1;
        }
        let word = &query[i..j];
        if matches!(word.to_ascii_lowercase().as_str(), "and" | "or" | "not") {
            return end;
        }
        if is_field_token(word) {
            return end;
        }
        end = j;
    }
    end
}

fn trim_value_span(query: &str, mut start: usize, mut end: usize) -> (usize, usize) {
    let bytes = query.as_bytes();
    while start < end && matches!(bytes[start], b' ' | b'\t') {
        start += 1;
    }
    while end > start && matches!(bytes[end - 1], b' ' | b'\t') {
        end -= 1;
    }
    (start, end)
}

fn highlight_re() -> &'static Regex {
    static CELL: OnceLock<Regex> = OnceLock::new();
    CELL.get_or_init(|| {
        let words: Vec<String> = scheme()
            .operators
            .iter()
            .filter(|op| op.as_str() != "-")
            .map(|op| regex::escape(op))
            .collect();
        let words = if words.is_empty() {
            vec!["AND".into(), "OR".into(), "NOT".into()]
        } else {
            words
        };
        let fields: String = field_names()
            .map(regex::escape)
            .collect::<Vec<_>>()
            .join("|");
        let pat = format!(
            r#"(?P<operator>\b(?:{})\b)|(?P<prohibit>-)?(?P<field>(?i:{})):(?P<value>\s*"[^"]*"|\s*[^\s)]*)"#,
            words.join("|"),
            fields
        );
        Regex::new(&pat).expect("catalog highlight pattern")
    })
}

pub fn suggest_last_token(query: &str, models: &[String], paths: &[String]) -> Vec<String> {
    let token = last_token(query);
    if token.is_empty() {
        return Vec::new();
    }
    if !token.contains(':') {
        let prefix = token.to_ascii_lowercase();
        return field_names()
            .filter(|n| n.starts_with(&prefix))
            .map(|n| format!("{n}:"))
            .collect();
    }
    let (field, rest) = token.split_once(':').unwrap_or(("", ""));
    let key = field.to_ascii_lowercase();
    let prefix = rest.to_ascii_lowercase();
    values_for_field(&key, models, paths)
        .into_iter()
        .filter(|v| v.to_ascii_lowercase().starts_with(&prefix))
        .map(|v| format!("{key}:{v}"))
        .collect()
}

pub fn apply_suggestion(query: &str, suggestion: &str) -> String {
    let stripped = query.trim_end();
    if stripped.is_empty() {
        return format!("{suggestion} ");
    }
    match stripped.rsplit_once(char::is_whitespace) {
        Some((head, _)) => format!("{head} {suggestion} "),
        None => format!("{suggestion} "),
    }
}

pub fn row_matches(row: &SessionListItem, query: &str) -> bool {
    let text = prepare_query(&finished_prefix(query));
    if text.trim().is_empty() {
        return true;
    }
    match parse_query(&text) {
        Ok(node) => eval(&node, row),
        Err(()) => {
            let words = bare_words(&text);
            words.is_empty()
                || words
                    .iter()
                    .all(|w| text_hay(row).contains(&w.to_ascii_lowercase()))
        }
    }
}

/// Same language as [`row_matches`] on a timeline event.
pub fn event_matches(ev: &TimelineEvent, query: &str) -> bool {
    let text = prepare_query(&finished_prefix(query));
    if text.trim().is_empty() {
        return true;
    }
    let hay = ev.haystack().to_ascii_lowercase();
    match parse_query(&text) {
        Ok(node) => eval_event(&node, ev, &hay),
        Err(()) => {
            let words = bare_words(&text);
            words.is_empty() || words.iter().all(|w| hay.contains(&w.to_ascii_lowercase()))
        }
    }
}

/// Same language as [`row_matches`] on a turn row.
pub fn turn_matches(turn: &TurnRow, query: &str) -> bool {
    let text = prepare_query(&finished_prefix(query));
    if text.trim().is_empty() {
        return true;
    }
    let hay = format!("{} {} {}", turn.label, turn.summary, turn.outcome).to_ascii_lowercase();
    match parse_query(&text) {
        Ok(node) => eval_turn(&node, turn, &hay),
        Err(()) => {
            let words = bare_words(&text);
            words.is_empty() || words.iter().all(|w| hay.contains(&w.to_ascii_lowercase()))
        }
    }
}

fn last_token(query: &str) -> String {
    let text = query.trim_end();
    text.rsplit_once(char::is_whitespace)
        .map(|(_, t)| t.to_string())
        .unwrap_or_else(|| text.to_string())
}

fn values_for_field(field: &str, models: &[String], paths: &[String]) -> Vec<String> {
    let closed = token_values(field);
    if !closed.is_empty() {
        return closed.to_vec();
    }
    if scheme().tokens.iter().any(|t| t.name == field && t.compare) {
        return scheme().compare.clone();
    }
    match field {
        "model" => unique_nonempty(models),
        "in" => unique_nonempty(paths)
            .into_iter()
            .map(|p| short_path(&p))
            .collect(),
        _ => Vec::new(),
    }
}

fn unique_nonempty(items: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    for item in items {
        let t = item.trim();
        if t.is_empty() || out.iter().any(|have: &String| have == t) {
            continue;
        }
        out.push(t.to_string());
    }
    out
}

fn short_path(path: &str) -> String {
    if let Some(home) = dirs_next_home() {
        if path == home || path.starts_with(&(home.clone() + "/")) {
            return format!("~{}", &path[home.len()..]);
        }
    }
    path.to_string()
}

fn dirs_next_home() -> Option<String> {
    std::env::var("HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("USERPROFILE").ok())
}

fn bare_words(text: &str) -> Vec<String> {
    text.split_whitespace()
        .filter(|w| !SKIP_WORDS.contains(&w.to_ascii_lowercase().as_str()) && !w.starts_with('('))
        .map(str::to_string)
        .collect()
}

fn text_hay(row: &SessionListItem) -> String {
    format!("{} {} {}", row.session_id, row.title, row.label).to_ascii_lowercase()
}

fn parse_query(raw: &str) -> Result<Node, ()> {
    let tokens = tokenize(raw)?;
    let mut i = 0;
    let node = parse_or(&tokens, &mut i)?;
    if i != tokens.len() {
        return Err(());
    }
    Ok(node)
}

#[derive(Debug, Clone, PartialEq)]
enum Tok {
    And,
    Or,
    Not,
    LParen,
    RParen,
    Word(String),
}

fn tokenize(raw: &str) -> Result<Vec<Tok>, ()> {
    let mut out = Vec::new();
    let mut rest = raw.trim();
    while !rest.is_empty() {
        rest = rest.trim_start();
        if rest.is_empty() {
            break;
        }
        if rest.starts_with('(') {
            out.push(Tok::LParen);
            rest = &rest[1..];
            continue;
        }
        if rest.starts_with(')') {
            out.push(Tok::RParen);
            rest = &rest[1..];
            continue;
        }
        if rest.starts_with('-') && rest.len() > 1 && rest.as_bytes()[1] != b' ' {
            out.push(Tok::Not);
            rest = &rest[1..];
            continue;
        }
        let (tok, n) = next_word(rest);
        rest = &rest[n..];
        // Only this spelling is an operator. `and` / `aNd` are ordinary words.
        out.push(match tok.as_str() {
            "AND" => Tok::And,
            "OR" => Tok::Or,
            "NOT" => Tok::Not,
            _ => Tok::Word(tok),
        });
    }
    Ok(out)
}

fn next_word(s: &str) -> (String, usize) {
    if let Some(rest) = s.strip_prefix('"') {
        if let Some(end) = rest.find('"') {
            return (rest[..end].to_string(), end + 2);
        }
    }
    if let Some(colon) = s.find(':') {
        let after = &s[colon + 1..];
        if let Some(inner) = after.strip_prefix('"') {
            if let Some(end) = inner.find('"') {
                let n = colon + 1 + 1 + end + 1;
                return (s[..n].to_string(), n);
            }
        }
    }
    let mut n = 0;
    for (i, c) in s.char_indices() {
        if c.is_ascii_whitespace() || c == '(' || c == ')' {
            break;
        }
        n = i + c.len_utf8();
    }
    (s[..n].to_string(), n)
}

fn prepare_query(query: &str) -> String {
    let mut out = String::new();
    let mut rest = query;
    while !rest.is_empty() {
        let prev_ok = out
            .chars()
            .last()
            .is_none_or(|c| !c.is_ascii_alphanumeric() && c != '_');
        let low = rest.to_ascii_lowercase();
        let field = ["after:", "before:"]
            .into_iter()
            .find(|name| low.starts_with(name));
        if prev_ok {
            if let Some(name) = field {
                let after = &rest[name.len()..];
                if !after.starts_with('"') {
                    let end = when_value_len(after);
                    out.push_str(name);
                    out.push('"');
                    out.push_str(after[..end].trim_end());
                    out.push('"');
                    rest = &after[end..];
                    continue;
                }
            }
        }
        let ch = rest.chars().next().unwrap();
        out.push(ch);
        rest = &rest[ch.len_utf8()..];
    }
    out
}

fn when_value_len(s: &str) -> usize {
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b')' {
            return i;
        }
        if bytes[i].is_ascii_whitespace() {
            let tail = s[i..].trim_start().to_ascii_lowercase();
            if keyword_at(&tail, "and") || keyword_at(&tail, "or") || keyword_at(&tail, "not") {
                return i;
            }
            if tail.starts_with(')') {
                return i;
            }
        }
        i += 1;
    }
    s.len()
}

fn keyword_at(low: &str, word: &str) -> bool {
    low.starts_with(word)
        && low
            .as_bytes()
            .get(word.len())
            .is_none_or(|b| !b.is_ascii_alphanumeric())
}

fn parse_or(tokens: &[Tok], i: &mut usize) -> Result<Node, ()> {
    let mut parts = vec![parse_and(tokens, i)?];
    while matches!(tokens.get(*i), Some(Tok::Or)) {
        *i += 1;
        parts.push(parse_and(tokens, i)?);
    }
    Ok(if parts.len() == 1 {
        parts.remove(0)
    } else {
        Node::Or(parts)
    })
}

fn parse_and(tokens: &[Tok], i: &mut usize) -> Result<Node, ()> {
    let mut parts = vec![parse_not(tokens, i)?];
    while let Some(tok) = tokens.get(*i) {
        match tok {
            Tok::Or | Tok::RParen => break,
            Tok::And => {
                *i += 1;
                parts.push(parse_not(tokens, i)?);
            }
            Tok::Not | Tok::LParen | Tok::Word(_) => parts.push(parse_not(tokens, i)?),
        }
    }
    Ok(if parts.len() == 1 {
        parts.remove(0)
    } else {
        Node::And(parts)
    })
}

fn parse_not(tokens: &[Tok], i: &mut usize) -> Result<Node, ()> {
    if matches!(tokens.get(*i), Some(Tok::Not)) {
        *i += 1;
        return Ok(Node::Not(Box::new(parse_not(tokens, i)?)));
    }
    parse_primary(tokens, i)
}

fn parse_primary(tokens: &[Tok], i: &mut usize) -> Result<Node, ()> {
    match tokens.get(*i) {
        Some(Tok::LParen) => {
            *i += 1;
            let inner = parse_or(tokens, i)?;
            if !matches!(tokens.get(*i), Some(Tok::RParen)) {
                return Err(());
            }
            *i += 1;
            Ok(inner)
        }
        Some(Tok::Word(w)) => {
            *i += 1;
            if let Some((field, value)) = w.split_once(':') {
                if field_names().any(|n| n == field.to_ascii_lowercase()) {
                    return Ok(Node::Pred {
                        field: field.to_ascii_lowercase(),
                        value: value.trim_matches('"').to_string(),
                    });
                }
            }
            Ok(Node::Text(w.clone()))
        }
        _ => Err(()),
    }
}

fn eval(node: &Node, row: &SessionListItem) -> bool {
    match node {
        Node::And(xs) => xs.iter().all(|n| eval(n, row)),
        Node::Or(xs) => xs.iter().any(|n| eval(n, row)),
        Node::Not(n) => !eval(n, row),
        Node::Pred { field, value } => eval_field(field, value, row),
        Node::Text(w) => text_hay(row).contains(&w.to_ascii_lowercase()),
    }
}

fn eval_event(node: &Node, ev: &TimelineEvent, hay: &str) -> bool {
    match node {
        Node::And(xs) => xs.iter().all(|n| eval_event(n, ev, hay)),
        Node::Or(xs) => xs.iter().any(|n| eval_event(n, ev, hay)),
        Node::Not(n) => !eval_event(n, ev, hay),
        Node::Pred { field, value } => match field.as_str() {
            "is" => event_is(ev, value),
            "has" => {
                let (name, cmp) = split_has_value(value);
                cmp.is_empty() && known_has_flag(&name) && name == "error" && ev.is_error
            }
            "errors" => num_cmp(i64::from(ev.is_error), value),
            _ => hay.contains(&format!("{field}:{value}").to_ascii_lowercase()),
        },
        Node::Text(w) => hay.contains(&w.to_ascii_lowercase()),
    }
}

fn event_is(ev: &TimelineEvent, value: &str) -> bool {
    let mode = match value.to_ascii_lowercase().as_str() {
        "tool" | "tools" => KindFilter::Tools,
        "user" => KindFilter::User,
        "assistant" | "asst" | "agent" => KindFilter::Asst,
        "session" | "sess" => KindFilter::Sess,
        "subagent" | "subagents" => KindFilter::Subagents,
        "background" => KindFilter::Background,
        "workflow" | "workflows" => KindFilter::Workflows,
        "error" | "errors" => KindFilter::Errors,
        _ => return false,
    };
    ev.matches_kind(mode)
}

fn eval_turn(node: &Node, turn: &TurnRow, hay: &str) -> bool {
    match node {
        Node::And(xs) => xs.iter().all(|n| eval_turn(n, turn, hay)),
        Node::Or(xs) => xs.iter().any(|n| eval_turn(n, turn, hay)),
        Node::Not(n) => !eval_turn(n, turn, hay),
        Node::Pred { field, value } => match field.as_str() {
            "has" => turn_has(turn, value),
            "errors" => num_cmp(turn.error_event_count.max(turn.tool_error_count), value),
            "tools" => num_cmp(turn.tool_call_count, value),
            "events" => num_cmp(turn.event_count, value),
            "duration" => num_cmp(turn.duration_seconds.unwrap_or(0.0) as i64, value),
            "subagents" => num_cmp(turn.subagent_runs.len() as i64, value),
            _ => hay.contains(&format!("{field}:{value}").to_ascii_lowercase()),
        },
        Node::Text(w) => hay.contains(&w.to_ascii_lowercase()),
    }
}

fn turn_has(turn: &TurnRow, value: &str) -> bool {
    let (name, cmp) = split_has_value(value);
    if !cmp.is_empty() || !known_has_flag(&name) {
        return false;
    }
    match name.as_str() {
        "error" => turn.error_event_count > 0 || turn.tool_error_count > 0,
        "subagent" => !turn.subagent_runs.is_empty(),
        _ => false,
    }
}

fn row_has_count(row: &SessionListItem, name: &str) -> i64 {
    let Some(wire) = count_wire(name) else {
        return i64::from(has_present(row, name));
    };
    match wire {
        "workflowCount" => row.workflow_count,
        "noteCount" => row.note_count,
        "goalCount" => row.goal_count,
        "planCount" => row.plan_count,
        "subagentCount" => row.subagent_count,
        "taskCount" => row.task_count,
        "jobCount" => row.job_count,
        "scheduleCount" => row.schedule_count,
        "errorCount" => row.error_count,
        "failureCount" => row.failure_count,
        "diffLineCount" => row.diff_line_count,
        "compactionCount" => row.compaction_count,
        "doomCount" => row.doom_count,
        _ => i64::from(has_present(row, name)),
    }
}

fn has_present(row: &SessionListItem, name: &str) -> bool {
    match name {
        "error" => row.error_count > 0,
        "git" => !row.git_repo.trim().is_empty(),
        "context" => {
            row.context_window_usage_pct.is_some()
                || row.context_tokens_used.is_some()
                || row.context_window_tokens.is_some_and(|window| window > 0)
        }
        "task" => row.has_jobs || row.has_schedules || row.task_count > 0,
        other => HAS_FLAGS
            .iter()
            .find(|(flag, _)| *flag == other)
            .is_some_and(|(_, flag)| flag(row)),
    }
}

fn match_has(value: &str, row: &SessionListItem) -> bool {
    let (name, cmp) = split_has_value(value);
    if !cmp.is_empty() || !known_has_flag(&name) {
        return false;
    }
    has_present(row, &name) || row_has_count(row, &name) > 0
}

fn eval_field(field: &str, value: &str, row: &SessionListItem) -> bool {
    match field {
        "is" => match value.to_ascii_lowercase().as_str() {
            "host" => row.origin.eq_ignore_ascii_case("host"),
            "eval" => !row.origin.eq_ignore_ascii_case("host"),
            "cancelled" | "canceled" => {
                let s = row.status.to_ascii_lowercase();
                s == "cancelled" || s == "canceled"
            }
            other => row.status.eq_ignore_ascii_case(other),
        },
        "has" => match_has(value, row),
        "in" => path_prefix(value, &row.run_dir),
        "model" => row
            .model
            .to_ascii_lowercase()
            .contains(&value.to_ascii_lowercase()),
        "task" => row
            .task_id
            .to_ascii_lowercase()
            .contains(&value.to_ascii_lowercase()),
        "after" => date_cmp(&row.updated_at, value, true),
        "before" => date_cmp(&row.updated_at, value, false),
        "turns" => num_cmp(row.turn_count, value),
        "tools" => num_cmp(row.tool_call_count, value),
        "events" => num_cmp(row.num_events, value),
        "duration" => num_cmp(row.duration_seconds as i64, value),
        other if count_wire(other).is_some() => num_cmp(row_has_count(row, other), value),
        _ => text_hay(row).contains(&format!("{field}:{value}").to_ascii_lowercase()),
    }
}

fn path_prefix(needle: &str, path: &str) -> bool {
    let want = expand_path(needle);
    let have = expand_path(path);
    if want.is_empty() || have.is_empty() {
        let raw = path.trim();
        let needle = needle.trim().trim_matches('"');
        return !raw.is_empty()
            && raw
                .to_ascii_lowercase()
                .contains(&needle.to_ascii_lowercase());
    }
    if have
        .to_ascii_lowercase()
        .contains(&want.to_ascii_lowercase())
    {
        return true;
    }
    have == want || have.starts_with(&(want.trim_end_matches('/').to_string() + "/"))
}

fn expand_path(raw: &str) -> String {
    let text = raw.trim().trim_matches('"');
    if text.is_empty() {
        return String::new();
    }
    let p = if let Some(rest) = text.strip_prefix("~/") {
        match dirs_next_home() {
            Some(h) => format!("{h}/{rest}"),
            None => text.to_string(),
        }
    } else if text == "~" {
        dirs_next_home().unwrap_or_else(|| text.to_string())
    } else {
        text.to_string()
    };
    Path::new(&p).to_string_lossy().into_owned()
}

fn date_cmp(updated: &str, raw: &str, after: bool) -> bool {
    let bound = parse_when(raw);
    if bound <= 0 {
        return true;
    }
    let stamp = parse_when(updated);
    if stamp <= 0 {
        return false;
    }
    if after {
        stamp >= bound
    } else {
        stamp <= bound
    }
}

fn parse_when(raw: &str) -> i64 {
    let text = raw.trim().trim_matches('"').trim_matches('\'');
    if text.is_empty() {
        return 0;
    }
    if let Some(iso) = parse_iso_epoch(text) {
        return iso;
    }
    if let Some(secs) = parse_relative_seconds(text) {
        return now_epoch().saturating_sub(secs);
    }
    0
}

fn now_epoch() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn parse_iso_epoch(text: &str) -> Option<i64> {
    let s = text.trim().trim_end_matches('Z');
    let (date, rest) = s.split_once('T').unwrap_or((s, "00:00:00"));
    if date.len() < 10
        || date.as_bytes().get(4) != Some(&b'-')
        || date.as_bytes().get(7) != Some(&b'-')
    {
        return None;
    }
    let y: i32 = date[0..4].parse().ok()?;
    let mo: u32 = date[5..7].parse().ok()?;
    let d: u32 = date[8..10].parse().ok()?;
    let time = rest.split(['+', '-']).next().unwrap_or(rest);
    let time = time.split('.').next().unwrap_or(time);
    let mut hm = time.split(':');
    let h: u32 = hm.next().unwrap_or("0").parse().ok()?;
    let mi: u32 = hm.next().unwrap_or("0").parse().unwrap_or(0);
    let se: u32 = hm.next().unwrap_or("0").parse().unwrap_or(0);
    Some(
        days_from_civil(y, mo, d)? * 86_400
            + i64::from(h) * 3600
            + i64::from(mi) * 60
            + i64::from(se),
    )
}

fn days_from_civil(y: i32, m: u32, d: u32) -> Option<i64> {
    if !(1..=12).contains(&m) || !(1..=31).contains(&d) {
        return None;
    }
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = if m > 2 { m - 3 } else { m + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe as u32 * 365 + yoe as u32 / 4 - yoe as u32 / 100 + doy;
    Some(i64::from(era) * 146_097 + i64::from(doe) - 719_468)
}

fn parse_relative_seconds(text: &str) -> Option<i64> {
    let low = text.trim().to_ascii_lowercase();
    if low == "yesterday" {
        return Some(86_400);
    }
    if let Some(s) = parse_compact_span(&low) {
        return Some(s);
    }
    let low = low.strip_suffix(" ago").unwrap_or(&low).trim();
    if let Some(s) = parse_compact_span(low) {
        return Some(s);
    }
    parse_words_span(low)
}

fn parse_words_span(text: &str) -> Option<i64> {
    let mut parts = text.split_whitespace();
    let n: f64 = parts.next()?.parse().ok()?;
    let unit = parts.next()?.trim_end_matches('s');
    if parts.next().is_some() {
        return None;
    }
    let mul = match unit {
        "second" => 1.0,
        "minute" => 60.0,
        "hour" => 3600.0,
        "day" => 86_400.0,
        "week" => 604_800.0,
        _ => return None,
    };
    Some((n * mul) as i64)
}

fn num_cmp(actual: i64, raw: &str) -> bool {
    let v = raw.trim();
    if let Some(n) = v.strip_prefix(">=") {
        return actual >= parse_seconds(n);
    }
    if let Some(n) = v.strip_prefix("<=") {
        return actual <= parse_seconds(n);
    }
    if let Some(n) = v.strip_prefix('>') {
        return actual > parse_seconds(n);
    }
    if let Some(n) = v.strip_prefix('<') {
        return actual < parse_seconds(n);
    }
    if let Some(n) = v.strip_prefix('=') {
        return actual == parse_seconds(n);
    }
    actual == parse_seconds(v)
}

fn parse_seconds(raw: &str) -> i64 {
    let text = raw.trim().trim_matches('"');
    if let Ok(n) = text.parse::<i64>() {
        return n;
    }
    parse_compact_span(text).unwrap_or(0)
}

fn parse_compact_span(text: &str) -> Option<i64> {
    let t = text.trim();
    let (digits, unit) = t.split_at(t.len().checked_sub(1)?);
    let amount: f64 = digits.parse().ok()?;
    let mul = match unit.to_ascii_lowercase().as_str() {
        "s" => 1.0,
        "m" => 60.0,
        "h" => 3600.0,
        "d" => 86_400.0,
        "w" => 604_800.0,
        _ => return None,
    };
    Some((amount * mul) as i64)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row() -> SessionListItem {
        SessionListItem {
            session_id: "sess-1".into(),
            title: "Fix the palette".into(),
            model: "grok-4".into(),
            status: "complete".into(),
            origin: "work".into(),
            path: "/mnt/dev/_git/fubar/sess-1".into(),
            git_repo: "https://github.com/x/fubar".into(),
            run_dir: "/mnt/dev/_git/fubar".into(),
            task_id: "eval-a".into(),
            error_count: 3,
            turn_count: 8,
            tool_call_count: 12,
            num_events: 40,
            duration_seconds: 4000.0,
            updated_at: "2026-08-10T12:00:00+00:00".into(),
            has_workflows: true,
            ..SessionListItem::default()
        }
    }

    #[test]
    fn bare_words_and_tokens() {
        let r = row();
        assert!(row_matches(&r, "palette"));
        assert!(!row_matches(&r, "_git/fubar"));
        assert!(row_matches(&r, "has:workflow AND errors:>2"));
        assert!(!row_matches(&r, "has:workflow AND errors:>20"));
        assert!(row_matches(&r, "is:eval"));
        assert!(!row_matches(&r, "is:host"));
        assert!(row_matches(&r, "NOT is:host"));
        assert!(row_matches(&r, "in:/mnt/dev/_git/fubar"));
        assert!(row_matches(&r, "model:grok"));
        assert_eq!(
            suggest_last_token("has:", &[], &[]),
            vec![
                "has:workflow",
                "has:note",
                "has:goal",
                "has:plan",
                "has:subagent",
                "has:task",
                "has:job",
                "has:schedule",
                "has:error",
                "has:failure",
                "has:diff",
                "has:compaction",
                "has:doom",
                "has:git",
                "has:context",
            ]
        );
        assert!(suggest_last_token("", &[], &[]).is_empty());
        assert!(suggest_last_token("   ", &[], &[]).is_empty());
    }

    #[test]
    fn highlight_spans_use_schema() {
        let marks = highlight_query_spans("has:goal AND has:gooals");
        let kinds: Vec<_> = marks
            .iter()
            .map(|s| (&"has:goal AND has:gooals"[s.start..s.end], s.kind))
            .collect();
        assert_eq!(
            kinds,
            vec![
                ("has:", QuerySpanKind::Field),
                ("goal", QuerySpanKind::Value),
                ("AND", QuerySpanKind::Operator),
                ("has:", QuerySpanKind::Field),
                ("gooals", QuerySpanKind::Unknown),
            ]
        );
        let lower = highlight_query_spans("and not has:goal");
        assert!(!lower.iter().any(|s| s.kind == QuerySpanKind::Operator));
        let mixed = highlight_query_spans("aNd has:goal");
        assert!(!mixed.iter().any(|s| s.kind == QuerySpanKind::Operator));
        let canceled = highlight_query_spans("is:canceled");
        assert_eq!(canceled.last().map(|s| s.kind), Some(QuerySpanKind::Value));
    }

    #[test]
    fn catalog_query_help_lists_schema_tokens() {
        let rows = catalog_query_help_rows();
        let blob = rows
            .iter()
            .map(|r| format!("{} {}", r.label, r.body))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(blob.contains("is:"));
        assert!(blob.contains("running"));
        assert!(blob.contains("has:"));
        assert!(blob.contains("workflows"));
        assert!(blob.contains("in:"));
        assert!(blob.contains("duration:"));
        assert!(blob.contains("OR"));
        assert!(blob.contains("has:plan"));
        assert!(blob.contains("plans:>=N"));
    }

    #[test]
    fn has_presence_tokens() {
        let empty = SessionListItem::default();
        assert!(!row_matches(&empty, "has:goal"));
        assert!(!row_matches(&empty, "has:task"));
        assert!(!row_matches(&empty, "has:git"));
        let full = SessionListItem {
            git_repo: "/tmp/repo".into(),
            error_count: 1,
            has_workflows: true,
            has_notes: true,
            has_goals: true,
            has_subagents: true,
            has_jobs: true,
            has_schedules: true,
            has_plan: true,
            has_failures: true,
            has_diff: true,
            has_compaction: true,
            has_doom: true,
            context_window_usage_pct: Some(10.0),
            ..SessionListItem::default()
        };
        for token in [
            "has:workflow",
            "has:note",
            "has:goal",
            "has:subagent",
            "has:task",
            "has:job",
            "has:schedule",
            "has:plan",
            "has:error",
            "has:failure",
            "has:diff",
            "has:git",
            "has:context",
            "has:compaction",
            "has:doom",
        ] {
            assert!(row_matches(&full, token), "{token}");
        }
        let jobs = SessionListItem {
            has_jobs: true,
            ..SessionListItem::default()
        };
        assert!(row_matches(&jobs, "has:job"));
        assert!(row_matches(&jobs, "has:task"));
        assert!(!row_matches(&jobs, "has:schedule"));
    }

    #[test]
    fn duration_and_human_when() {
        let r = row();
        assert!(row_matches(&r, "duration:>1h"));
        assert!(row_matches(&r, "duration:>30m"));
        assert!(!row_matches(&r, "duration:>2h"));
        assert!(row_matches(&r, "after:2026-08-01"));
        assert!(!row_matches(&r, "before:2026-08-01"));
        let mut recent = row();
        recent.updated_at = "2099-01-01T00:00:00+00:00".into();
        let mut old = row();
        old.updated_at = "2000-01-01T00:00:00+00:00".into();
        assert!(row_matches(&recent, "after:yesterday"));
        assert!(row_matches(&recent, "after:2d"));
        assert!(row_matches(&recent, "after:2 days ago"));
        assert!(!row_matches(&old, "after:yesterday"));
        assert!(!row_matches(&old, "after:2d"));
        assert!(!row_matches(&old, "after:2 days ago"));
        assert!(row_matches(&old, "before:yesterday"));
        assert!(row_matches(&old, "before:2 days ago"));
        assert!(row_matches(&r, "after:2026-08-01"));
        assert!(!row_matches(&r, "before:2026-08-01"));
    }

    #[test]
    fn has_quantity_compare() {
        let r = SessionListItem {
            title: "palette".into(),
            status: "running".into(),
            error_count: 5,
            workflow_count: 3,
            note_count: 2,
            has_workflows: true,
            has_notes: true,
            ..SessionListItem::default()
        };
        assert!(row_matches(&r, "has:error"));
        assert!(row_matches(&r, "errors:>=5"));
        assert!(row_matches(&r, "errors:5"));
        assert!(!row_matches(&r, "errors:>=6"));
        assert!(!row_matches(&r, "has:error:>=5"));
        assert!(row_matches(&r, "has:workflow"));
        assert!(row_matches(&r, "workflows:>=2"));
        assert!(row_matches(&r, "workflows:3"));
        assert!(!row_matches(&r, "workflows:>=4"));
        assert!(row_matches(&r, "workflows:>=2 AND NOT is:complete"));
        assert!(row_matches(&r, "notes:>=2 AND errors:>=5"));
        let goals = SessionListItem {
            has_goals: true,
            goal_count: 1,
            ..SessionListItem::default()
        };
        assert!(row_matches(&goals, "has:goal"));
        assert!(row_matches(&goals, "goals:>=1"));
        assert!(row_matches(&goals, "goals:1"));
        assert!(!row_matches(&goals, "goals:2"));
        assert!(!row_matches(&goals, "goals:>2"));
        assert!(!row_matches(&goals, "has:goal:2"));
    }

    #[test]
    fn highlight_has_quantity_spans() {
        let q = "workflows:>=2";
        let kinds: Vec<_> = highlight_query_spans(q)
            .into_iter()
            .map(|s| (&q[s.start..s.end], s.kind))
            .collect();
        assert_eq!(
            kinds,
            vec![
                ("workflows:", QuerySpanKind::Field),
                (">=2", QuerySpanKind::Value),
            ]
        );
        let g = "goals:2";
        let kinds: Vec<_> = highlight_query_spans(g)
            .into_iter()
            .map(|s| (&g[s.start..s.end], s.kind))
            .collect();
        assert_eq!(
            kinds,
            vec![
                ("goals:", QuerySpanKind::Field),
                ("2", QuerySpanKind::Value),
            ]
        );
        let old = "has:goal:2";
        let kinds: Vec<_> = highlight_query_spans(old)
            .into_iter()
            .map(|s| (&old[s.start..s.end], s.kind))
            .collect();
        assert_eq!(
            kinds,
            vec![
                ("has:", QuerySpanKind::Field),
                ("goal:2", QuerySpanKind::Unknown),
            ]
        );
    }

    #[test]
    fn operators_are_uppercase_only() {
        let r = SessionListItem {
            title: "palette".into(),
            ..SessionListItem::default()
        };
        assert!(row_matches(&r, "palette AND NOT has:note"));
        assert!(!row_matches(&r, "palette and not has:note"));
        assert!(!row_matches(&r, "palette aNd NOT has:note"));
        assert!(!row_matches(&r, "missing OR has:note"));
        assert!(row_matches(&r, "missing OR NOT has:note"));
    }

    #[test]
    fn in_matches_run_dir_substring() {
        let r = row();
        assert!(row_matches(&r, "in:/mnt/dev/_git/fubar"));
        assert!(row_matches(&r, "in:fubar"));
        assert!(row_matches(&r, "in:FUBAR"));
        assert!(!row_matches(&r, "in:/mnt/dev/_git/other"));
        let empty = SessionListItem {
            git_repo: "https://github.com/x/fubar".into(),
            run_dir: String::new(),
            ..SessionListItem::default()
        };
        assert!(!row_matches(&empty, "in:fubar"));
    }

    #[test]
    fn event_and_turn_use_same_language() {
        let ev = TimelineEvent {
            event_type: "tool_call".into(),
            kind: "tool".into(),
            tool_name: "read_file".into(),
            content: "hello user".into(),
            is_error: true,
            ..TimelineEvent::default()
        };
        assert!(event_matches(&ev, "hello"));
        assert!(event_matches(&ev, "has:error"));
        assert!(event_matches(&ev, "errors:>=1"));
        assert!(event_matches(&ev, "is:tool AND has:error"));
        assert!(!event_matches(&ev, "is:user"));
        assert!(!event_matches(&ev, "is:workflow"));
        assert!(!event_matches(&ev, "has:error:>=1"));
        let turn = TurnRow {
            label: "paint the list".into(),
            summary: "did the work".into(),
            outcome: "success".into(),
            error_event_count: 2,
            tool_call_count: 4,
            event_count: 10,
            duration_seconds: Some(90.0),
            subagent_runs: vec![crate::wire::SubagentRunRow::default()],
            ..TurnRow::default()
        };
        assert!(turn_matches(&turn, "paint AND errors:>=2 AND has:subagent"));
        let quiet = TurnRow {
            label: "paint the list".into(),
            ..TurnRow::default()
        };
        assert!(!turn_matches(&quiet, "has:subagent"));
    }
}
