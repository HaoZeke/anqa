//! Streaming jsonl. Never load the whole file as one string.

use serde_json::Value;
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::Path;

const TAIL_BYTES: u64 = 64 * 1024;
const HEAD_LIMIT: usize = 16;
const TAIL_LIMIT: usize = 16;

/// One jsonl object plus the original line text.
#[derive(Clone, Debug)]
pub struct JsonlRow {
    pub raw: String,
    pub value: Value,
}

/// Parse one line as a JSON object.
#[must_use]
pub fn object_line(line: &str) -> Option<Value> {
    let val: Value = serde_json::from_str(line.trim()).ok()?;
    val.is_object().then_some(val)
}

/// Stream every object row. Junk lines are skipped.
pub fn read_objects(path: &Path) -> Vec<JsonlRow> {
    let file = match File::open(path) {
        Ok(f) => f,
        Err(_) => return Vec::new(),
    };
    let reader = BufReader::new(file);
    let mut out = Vec::new();
    for line in reader.lines() {
        let Ok(line) = line else { continue };
        if line.trim().is_empty() {
            continue;
        }
        if let Some(value) = object_line(&line) {
            out.push(JsonlRow { raw: line, value });
        }
    }
    out
}

fn first_objects(path: &Path, limit: usize) -> Vec<JsonlRow> {
    let file = match File::open(path) {
        Ok(f) => f,
        Err(_) => return Vec::new(),
    };
    let reader = BufReader::new(file);
    let mut out = Vec::new();
    for line in reader.lines() {
        let Ok(line) = line else { continue };
        if line.trim().is_empty() {
            continue;
        }
        if let Some(value) = object_line(&line) {
            out.push(JsonlRow { raw: line, value });
            if out.len() >= limit {
                break;
            }
        }
    }
    out
}

fn last_objects(path: &Path, limit: usize) -> Vec<JsonlRow> {
    let mut file = match File::open(path) {
        Ok(f) => f,
        Err(_) => return Vec::new(),
    };
    let size = file.metadata().map(|m| m.len()).unwrap_or(0);
    if size > TAIL_BYTES {
        if file.seek(SeekFrom::Start(size - TAIL_BYTES)).is_err() {
            return Vec::new();
        }
        let mut discard = String::new();
        let mut r = BufReader::new(&mut file);
        let _ = r.read_line(&mut discard);
        return rows_from_reader(r, limit, true);
    }
    rows_from_reader(BufReader::new(file), limit, true)
}

fn rows_from_reader<R: Read>(reader: BufReader<R>, limit: usize, keep_tail: bool) -> Vec<JsonlRow> {
    let mut out = Vec::new();
    for line in reader.lines() {
        let Ok(line) = line else { continue };
        if line.trim().is_empty() {
            continue;
        }
        if let Some(value) = object_line(&line) {
            out.push(JsonlRow { raw: line, value });
        }
    }
    if keep_tail && out.len() > limit {
        out.split_off(out.len() - limit)
    } else {
        out
    }
}

/// Header plus tail objects. Files at or under 64 KiB are read once.
pub fn window(path: &Path) -> Vec<JsonlRow> {
    let size = match path.metadata() {
        Ok(m) => m.len(),
        Err(_) => return Vec::new(),
    };
    if size <= TAIL_BYTES {
        return last_objects(path, 10_000);
    }
    let mut out = first_objects(path, HEAD_LIMIT);
    out.extend(last_objects(path, TAIL_LIMIT));
    out
}

/// First object, if any.
#[must_use]
pub fn first_object(path: &Path) -> Option<JsonlRow> {
    first_objects(path, 1).into_iter().next()
}

/// File stamp used as a timeline cache key.
#[must_use]
pub fn file_stamp(path: &Path) -> (f64, u64, u64, u64) {
    match path.metadata() {
        Ok(m) => {
            let mtime = m
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);
            (mtime, m.len(), 0, 0)
        }
        Err(_) => (0.0, 0, 0, 0),
    }
}
