//! Catalog query language. Tokens come from the published control contract
//! (`desktop/assets/catalog-query.json`, same `catalogQuery` as the schema).

use std::path::Path;
use std::sync::OnceLock;

use serde::Deserialize;

use crate::wire::SessionListItem;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct QueryScheme {
    compare: Vec<String>,
    tokens: Vec<QueryTokenSpec>,
}

#[derive(Debug, Deserialize)]
struct QueryTokenSpec {
    name: String,
    #[serde(default)]
    values: Vec<String>,
    #[serde(default)]
    compare: bool,
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

/// Presence flags for ``has:`` (row attributes, not the token list).
type HasFlag = fn(&SessionListItem) -> bool;
const HAS_FLAGS: &[(&str, HasFlag)] = &[
    ("workflows", |r| r.has_workflows),
    ("notes", |r| r.has_notes),
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

pub fn suggest_last_token(query: &str, models: &[String], paths: &[String]) -> Vec<String> {
    let token = last_token(query);
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
        let low = tok.to_ascii_lowercase();
        out.push(match low.as_str() {
            "and" => Tok::And,
            "or" => Tok::Or,
            "not" => Tok::Not,
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

fn match_has(value: &str, row: &SessionListItem) -> bool {
    let key = value.to_ascii_lowercase();
    if key == "errors" {
        return row.error_count > 0;
    }
    HAS_FLAGS
        .iter()
        .find(|(name, _)| *name == key)
        .is_some_and(|(_, flag)| flag(row))
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
        "in" => path_prefix(value, &row.git_repo),
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
        "errors" => num_cmp(row.error_count, value),
        "turns" => num_cmp(row.turn_count, value),
        "tools" => num_cmp(row.tool_call_count, value),
        "events" => num_cmp(row.num_events, value),
        "duration" => num_cmp(row.duration_seconds as i64, value),
        _ => text_hay(row).contains(&format!("{field}:{value}").to_ascii_lowercase()),
    }
}

fn path_prefix(needle: &str, path: &str) -> bool {
    let want = expand_path(needle);
    let have = expand_path(path);
    if want.is_empty() {
        return false;
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

/// True when ``after:`` / ``before:`` needs ``groket serve`` (human phrases).
pub fn needs_control_when(query: &str) -> bool {
    let text = finished_prefix(query).to_ascii_lowercase();
    for tok in text.split(|c: char| c.is_ascii_whitespace() || c == '(' || c == ')') {
        let (field, value) = match tok.split_once(':') {
            Some(parts) => parts,
            None => continue,
        };
        if field != "after" && field != "before" {
            continue;
        }
        let value = value.trim_matches('"');
        if value.is_empty() {
            continue;
        }
        if parse_day(value).is_none() {
            return true;
        }
    }
    false
}

fn date_cmp(updated: &str, raw: &str, after: bool) -> bool {
    match (parse_day(updated), parse_day(raw)) {
        (Some(s), Some(b)) => {
            if after {
                s >= b
            } else {
                s <= b
            }
        }
        // ``yesterday`` / ``2d`` / ``2 days ago`` are resolved by groket serve.
        _ => true,
    }
}

fn parse_day(raw: &str) -> Option<String> {
    let t = raw.trim().trim_matches('"');
    if t.len() >= 10 && t.as_bytes()[4] == b'-' && t.as_bytes()[7] == b'-' {
        Some(t[..10].to_string())
    } else {
        None
    }
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
            git_repo: "/mnt/dev/_git/fubar".into(),
            task_id: "eval-a".into(),
            error_count: 3,
            turn_count: 8,
            tool_call_count: 12,
            num_events: 40,
            duration_seconds: 4000.0,
            updated_at: "2026-08-10T12:00:00+00:00".into(),
            has_workflows: true,
            has_findings: true,
            ..SessionListItem::default()
        }
    }

    #[test]
    fn bare_words_and_tokens() {
        let r = row();
        assert!(row_matches(&r, "palette"));
        assert!(!row_matches(&r, "_git/fubar"));
        assert!(row_matches(&r, "has:workflows AND errors:>2"));
        assert!(!row_matches(&r, "has:workflows AND errors:>20"));
        assert!(row_matches(&r, "is:eval"));
        assert!(!row_matches(&r, "is:host"));
        assert!(row_matches(&r, "NOT is:host"));
        assert!(row_matches(&r, "in:/mnt/dev/_git/fubar"));
        assert!(row_matches(&r, "model:grok"));
        assert_eq!(
            suggest_last_token("has:", &[], &[]),
            vec!["has:workflows", "has:notes", "has:errors",]
        );
    }

    #[test]
    fn duration_and_human_when() {
        let r = row();
        assert!(row_matches(&r, "duration:>1h"));
        assert!(row_matches(&r, "duration:>30m"));
        assert!(!row_matches(&r, "duration:>2h"));
        assert!(row_matches(&r, "after:2026-08-01"));
        assert!(!row_matches(&r, "before:2026-08-01"));
        assert!(needs_control_when("after:yesterday"));
        assert!(needs_control_when("before:2 days ago"));
        assert!(!needs_control_when("after:2026-08-01"));
        // Human phrases stay visible until serve applies them.
        assert!(row_matches(&r, "after:yesterday"));
    }
}
