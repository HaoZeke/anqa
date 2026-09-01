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
pub fn parse_ts_value(val: &Value) -> Option<i64> {
    if let Some(n) = val.as_i64() {
        return Some(n);
    }
    if let Some(n) = val.as_u64() {
        return Some(n as i64);
    }
    if let Some(n) = val.as_f64() {
        return Some(n as i64);
    }
    val.as_str().and_then(parse_time_str)
}

fn parse_time_str(s: &str) -> Option<i64> {
    let t = s.trim();
    if t.is_empty() {
        return None;
    }
    if let Ok(n) = t.parse::<i64>() {
        return Some(n);
    }
    if let Ok(n) = t.parse::<f64>() {
        return Some(n as i64);
    }
    parse_iso8601(t)
}

fn parse_iso8601(s: &str) -> Option<i64> {
    let s = s.trim();
    let (main, z) = if let Some(rest) = s.strip_suffix('Z').or_else(|| s.strip_suffix('z')) {
        (rest, 0i64)
    } else if let Some(idx) = s.rfind('+') {
        (s.get(..idx)?, parse_offset(s.get(idx..)?)?)
    } else if let Some(idx) = s[11..].rfind('-') {
        let abs = 11 + idx;
        (s.get(..abs)?, -parse_offset(s.get(abs..)?)?)
    } else {
        (s, 0)
    };
    let (date, time) = main.split_once('T').or_else(|| main.split_once(' '))?;
    let mut d = date.split('-');
    let year: i64 = d.next()?.parse().ok()?;
    let month: i64 = d.next()?.parse().ok()?;
    let day: i64 = d.next()?.parse().ok()?;
    let (hms, frac) = time.split_once('.').unwrap_or((time, "0"));
    let mut t = hms.split(':');
    let hour: i64 = t.next()?.parse().ok()?;
    let minute: i64 = t.next()?.parse().ok()?;
    let second: i64 = t.next()?.parse().ok()?;
    let _ = frac;
    let days = days_from_civil(year, month, day)?;
    Some(days * 86400 + hour * 3600 + minute * 60 + second - z)
}

fn parse_offset(s: &str) -> Option<i64> {
    let s = s.trim_start_matches(['+', '-']);
    if s.len() < 2 {
        return None;
    }
    let hour: i64 = s.get(..2)?.parse().ok()?;
    let minute: i64 = if s.len() >= 5 {
        s.get(3..5)?.parse().ok()?
    } else {
        0
    };
    Some(hour * 3600 + minute * 60)
}

fn days_from_civil(y: i64, m: i64, d: i64) -> Option<i64> {
    if !(1..=12).contains(&m) || !(1..=31).contains(&d) {
        return None;
    }
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = if m > 2 { m - 3 } else { m + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Some(era * 146097 + doe - 719468)
}

#[must_use]
pub fn epoch(val: &Value) -> Option<i64> {
    val.get("timestamp")
        .or_else(|| val.get("ts"))
        .and_then(parse_ts_value)
        .or_else(|| parse_ts_value(val))
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn iso_zulu_to_epoch() {
        assert_eq!(parse_iso8601("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(parse_iso8601("1970-01-01T00:00:01Z"), Some(1));
        let via = parse_ts_value(&json!("2026-08-28T17:37:29Z")).unwrap();
        assert!(via > 1_700_000_000);
        let obj = json!({"ts": "2026-08-28T17:37:29.225Z"});
        assert_eq!(epoch(&obj), Some(via));
    }
}
