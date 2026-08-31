//! Saved search filters from ``filters/list`` (no local TOML parse).

use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum FilterForm {
    #[default]
    Closed,
    Save,
    Holes,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FilterHoleKind {
    Choice,
    Text,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FilterHole {
    pub field: String,
    pub kind: FilterHoleKind,
    pub choices: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SavedFilter {
    pub name: String,
    pub scope: String,
    pub query: String,
    pub holes: Vec<FilterHole>,
}

pub fn parse_list(value: &Value) -> Vec<SavedFilter> {
    value
        .get("filters")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(parse_row)
        .collect()
}

pub fn parse_row(value: &Value) -> Option<SavedFilter> {
    let name = value.get("name")?.as_str()?.trim();
    let scope = value.get("scope")?.as_str()?.trim();
    let query = value.get("query")?.as_str()?.trim();
    if name.is_empty() || scope.is_empty() || query.is_empty() {
        return None;
    }
    let holes = value
        .get("holes")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(parse_hole)
        .collect();
    Some(SavedFilter {
        name: name.to_string(),
        scope: scope.to_string(),
        query: query.to_string(),
        holes,
    })
}

fn parse_hole(value: &Value) -> Option<FilterHole> {
    let field = value.get("field")?.as_str()?.trim();
    if field.is_empty() {
        return None;
    }
    let kind = match value.get("kind").and_then(Value::as_str).unwrap_or("text") {
        "choice" => FilterHoleKind::Choice,
        _ => FilterHoleKind::Text,
    };
    let choices = value
        .get("choices")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect();
    Some(FilterHole {
        field: field.to_string(),
        kind,
        choices,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parse_list_reads_holes() {
        let rows = parse_list(&json!({
            "filters": [{
                "name": "Harness",
                "scope": "catalog",
                "query": "harness:{grok,claude} AND in:?",
                "holes": [
                    {"field": "harness", "kind": "choice", "choices": ["grok", "claude"]},
                    {"field": "in", "kind": "text", "choices": []}
                ]
            }]
        }));
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].name, "Harness");
        assert_eq!(rows[0].holes[0].kind, FilterHoleKind::Choice);
        assert_eq!(rows[0].holes[0].choices, ["grok", "claude"]);
        assert_eq!(rows[0].holes[1].kind, FilterHoleKind::Text);
    }

    #[test]
    fn parse_list_skips_empty_name() {
        let rows = parse_list(&json!({
            "filters": [{"name": " ", "scope": "catalog", "query": "has:note"}]
        }));
        assert!(rows.is_empty());
    }
}
