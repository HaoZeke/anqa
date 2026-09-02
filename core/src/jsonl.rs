//! Streaming jsonl. Never load the whole file as one string.

use serde_json::Value;
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex};

const TAIL_BYTES: u64 = 64 * 1024;
const HEAD_LIMIT: usize = 16;
const TAIL_LIMIT: usize = 16;

/// One jsonl object plus the original line text.
#[derive(Clone, Debug)]
pub struct JsonlRow {
    pub raw: String,
    pub value: Value,
}

/// Append-only byte cursor over a jsonl file.
pub struct JsonlCursor {
    pub byte_pos: u64,
    pub records: Vec<JsonlRow>,
    keep: Option<fn(&[u8]) -> bool>,
}

impl JsonlCursor {
    /// Empty cursor that keeps every object line.
    #[must_use]
    pub fn new() -> Self {
        Self {
            byte_pos: 0,
            records: Vec::new(),
            keep: None,
        }
    }

    /// Cursor that drops a line when `keep` is false, before `from_utf8`.
    #[must_use]
    pub fn with_keep(keep: fn(&[u8]) -> bool) -> Self {
        Self {
            byte_pos: 0,
            records: Vec::new(),
            keep: Some(keep),
        }
    }

    /// Resume from `byte_pos`. Shrink resets; growth seeks and extends.
    pub fn sync(&mut self, path: &Path) {
        let size = match path.metadata() {
            Ok(m) => m.len(),
            Err(_) => {
                self.byte_pos = 0;
                self.records.clear();
                return;
            }
        };
        if size < self.byte_pos {
            self.byte_pos = 0;
            self.records.clear();
        }
        if size == self.byte_pos {
            return;
        }
        let mut file = match File::open(path) {
            Ok(f) => f,
            Err(_) => {
                self.byte_pos = 0;
                self.records.clear();
                return;
            }
        };
        if self.byte_pos > 0 && file.seek(SeekFrom::Start(self.byte_pos)).is_err() {
            self.byte_pos = 0;
            self.records.clear();
            if file.seek(SeekFrom::Start(0)).is_err() {
                return;
            }
        }
        let mut reader = BufReader::new(file);
        let mut buf = Vec::new();
        loop {
            buf.clear();
            match reader.read_until(b'\n', &mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    if !buf.ends_with(b"\n") {
                        break;
                    }
                    self.byte_pos += n as u64;
                    self.take_line(&buf);
                }
                Err(_) => break,
            }
        }
    }

    fn take_line(&mut self, buf: &[u8]) {
        let line = buf.strip_suffix(b"\n").unwrap_or(buf);
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        if line.iter().all(|b| b.is_ascii_whitespace()) {
            return;
        }
        if self.keep.is_some_and(|keep| !keep(line)) {
            return;
        }
        let Ok(text) = std::str::from_utf8(line) else {
            return;
        };
        if let Some(value) = object_line(text) {
            self.records.push(JsonlRow {
                raw: text.to_string(),
                value,
            });
        }
    }
}

impl Default for JsonlCursor {
    fn default() -> Self {
        Self::new()
    }
}

static CURSORS: LazyLock<Mutex<HashMap<PathBuf, JsonlCursor>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

/// Records for `path`, resuming an in-process cursor when the file grew.
pub fn cached_records(path: &Path, keep: Option<fn(&[u8]) -> bool>) -> Vec<JsonlRow> {
    let Ok(mut guard) = CURSORS.lock() else {
        let mut cursor = match keep {
            Some(keep) => JsonlCursor::with_keep(keep),
            None => JsonlCursor::new(),
        };
        cursor.sync(path);
        return cursor.records;
    };
    let cursor = guard
        .entry(path.to_path_buf())
        .or_insert_with(|| match keep {
            Some(keep) => JsonlCursor::with_keep(keep),
            None => JsonlCursor::new(),
        });
    cursor.sync(path);
    cursor.records.clone()
}

/// Parse one line as a JSON object.
#[must_use]
pub fn object_line(line: &str) -> Option<Value> {
    let val: Value = serde_json::from_str(line.trim()).ok()?;
    val.is_object().then_some(val)
}

/// Stream every object row. Junk lines are skipped.
pub fn read_objects(path: &Path) -> Vec<JsonlRow> {
    let mut cursor = JsonlCursor::new();
    cursor.sync(path);
    cursor.records
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

/// Objects in the last 64 KiB. Small files are read whole.
#[must_use]
pub fn tail(path: &Path) -> Vec<JsonlRow> {
    last_objects(path, 10_000)
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
pub fn file_stamp(path: &Path) -> crate::event::FileStamp {
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

/// First file mtime/size in slots 0-1, second file mtime/size in slots 2-3.
#[must_use]
pub fn pair_stamp(first: &Path, second: &Path) -> crate::event::FileStamp {
    let (mtime, size, _, _) = file_stamp(first);
    let (other_mtime, other_size, _, _) = file_stamp(second);
    (mtime, size, other_mtime as u64, other_size)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io::Write;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_jsonl(label: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "anqa-jsonl-{label}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir.join("sess.jsonl")
    }

    #[test]
    fn jsonl_cursor_appends_without_reread() {
        let path = temp_jsonl("append");
        fs::write(&path, "{\"a\":1}\n").unwrap();

        let mut cursor = JsonlCursor::new();
        cursor.sync(&path);
        assert_eq!(cursor.records.len(), 1);
        assert_eq!(cursor.records[0].value["a"], 1);
        let pos_after_first = cursor.byte_pos;
        assert!(pos_after_first > 0);

        let mut file = fs::OpenOptions::new().append(true).open(&path).unwrap();
        writeln!(file, r#"{{"b":2}}"#).unwrap();
        drop(file);

        cursor.sync(&path);
        assert_eq!(cursor.records.len(), 2, "append must add one record");
        assert!(cursor.byte_pos > pos_after_first);
        assert_eq!(cursor.records[0].value["a"], 1);
        assert_eq!(cursor.records[1].value["b"], 2);

        fs::write(&path, "{\"c\":3}\n").unwrap();
        cursor.sync(&path);
        assert_eq!(cursor.records.len(), 1, "truncate must drop the stale tail");
        assert_eq!(cursor.records[0].value["c"], 3);

        let _ = fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn jsonl_skip_line_without_string() {
        let mut line = br#"{"x":""#.to_vec();
        line.extend(std::iter::repeat(b'x').take(2 * 1024 * 1024));
        line.push(0xFF);
        line.push(b'\n');
        line.extend_from_slice(br#"{"ok":true}"#);
        line.push(b'\n');

        let path = temp_jsonl("skip");
        fs::write(&path, &line).unwrap();

        let mut cursor = JsonlCursor::with_keep(|_| false);
        cursor.sync(&path);
        assert!(
            cursor.records.is_empty(),
            "keep-fn false must skip before from_utf8"
        );
        assert_eq!(cursor.byte_pos, line.len() as u64);

        let _ = fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn cached_records_resume_on_append() {
        let path = temp_jsonl("cache");
        fs::write(&path, "{\"a\":1}\n").unwrap();
        assert_eq!(cached_records(&path, None).len(), 1);

        let mut file = fs::OpenOptions::new().append(true).open(&path).unwrap();
        writeln!(file, r#"{{"b":2}}"#).unwrap();
        drop(file);
        assert_eq!(cached_records(&path, None).len(), 2);

        fs::write(&path, "{\"c\":3}\n").unwrap();
        let rows = cached_records(&path, None);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].value["c"], 3);

        let _ = fs::remove_dir_all(path.parent().unwrap());
    }
}
