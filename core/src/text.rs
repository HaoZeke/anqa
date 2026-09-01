//! JSON value helpers shared by every store parser.

use serde_json::Value;

#[must_use]
pub fn as_str(val: &Value) -> String {
    match val {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

#[must_use]
pub fn field_str(obj: &Value, key: &str) -> String {
    obj.get(key).map(as_str).unwrap_or_default()
}

#[must_use]
pub fn field_i64(obj: &Value, key: &str) -> Option<i64> {
    let val = obj.get(key)?;
    if let Some(n) = val.as_i64() {
        return Some(n);
    }
    if let Some(n) = val.as_f64() {
        return Some(n as i64);
    }
    if let Some(s) = val.as_str() {
        if let Ok(n) = s.parse::<i64>() {
            return Some(n);
        }
        if let Ok(n) = s.parse::<f64>() {
            return Some(n as i64);
        }
    }
    None
}

/// Concatenate text from a string or a list of content blocks.
#[must_use]
pub fn text_of(val: &Value) -> String {
    match val {
        Value::String(s) => s.clone(),
        Value::Array(items) => {
            let mut bits = Vec::new();
            for item in items {
                if let Value::String(s) = item {
                    if !s.trim().is_empty() {
                        bits.push(s.clone());
                    }
                } else if let Some(text) = item.get("text") {
                    let s = as_str(text);
                    if !s.is_empty() {
                        bits.push(s);
                    }
                } else if let Some(text) = item.get("thinking") {
                    let s = as_str(text);
                    if !s.is_empty() {
                        bits.push(s);
                    }
                }
            }
            bits.join("\n")
        }
        Value::Object(_) => field_str(val, "text"),
        _ => String::new(),
    }
}

#[must_use]
pub fn epoch(val: &Value) -> Option<i64> {
    field_i64(val, "timestamp")
        .or_else(|| val.as_i64())
        .or_else(|| val.as_f64().map(|n| n as i64))
        .or_else(|| {
            val.as_str().and_then(|s| {
                s.parse::<i64>()
                    .ok()
                    .or_else(|| s.parse::<f64>().ok().map(|n| n as i64))
            })
        })
}

/// ISO-ish stamp from epoch seconds or a string field.
#[must_use]
pub fn iso_from(val: &Value) -> String {
    if let Some(s) = val.as_str() {
        if !s.is_empty() {
            return s.to_string();
        }
    }
    String::new()
}

pub fn index_events(events: &mut [crate::event::Event]) {
    for (i, ev) in events.iter_mut().enumerate() {
        ev.index = i as u32;
    }
}
