//! Byte-needle prefilter for Grok ``updates.jsonl`` lines.
//!
//! Streaming ``tool_call_update`` rows often *are* the multi-100MB file
//! (cumulative shell output). Skip JSON parse unless the line looks terminal.

/// Same needle as ``anqa.scan``.
const TU_BYTES: &[u8] = b"tool_call_update";

/// Same needles as ``anqa.scan`` (spacing variants included).
const TERM_BYTES: &[&[u8]] = &[
    br#""status":"completed""#,
    br#""status": "completed""#,
    br#""status":"failed""#,
    br#""status": "failed""#,
    br#""isError":true"#,
    br#""isError": true"#,
];

fn contains_bytes(hay: &[u8], needle: &[u8]) -> bool {
    memchr::memmem::find(hay, needle).is_some()
}

fn strip_trailing_cr(line: &[u8]) -> &[u8] {
    line.strip_suffix(b"\r").unwrap_or(line)
}

/// Return ``false`` when a ``tool_call_update`` line is non-terminal (skip JSON).
///
/// Keep the line when it is not a ``tool_call_update``, or when it contains any
/// terminal status / error needle.
#[must_use]
pub fn keep_updates_line(line: &[u8]) -> bool {
    if contains_bytes(line, TU_BYTES)
        && !TERM_BYTES.iter().copied().any(|m| contains_bytes(line, m))
    {
        return false;
    }
    true
}

/// Split *data* on ``\\n`` and keep lines [`keep_updates_line`] accepts.
///
/// Trailing ``\\r`` is dropped. An incomplete last line (no ``\\n``) is kept
/// when nonempty.
#[must_use]
pub fn filter_updates(data: &[u8]) -> Vec<Vec<u8>> {
    let mut out = Vec::new();
    let mut start = 0usize;
    for (i, &b) in data.iter().enumerate() {
        if b != b'\n' {
            continue;
        }
        let line = strip_trailing_cr(&data[start..i]);
        if keep_updates_line(line) {
            out.push(line.to_vec());
        }
        start = i + 1;
    }
    if start < data.len() {
        let line = strip_trailing_cr(&data[start..]);
        if !line.is_empty() && keep_updates_line(line) {
            out.push(line.to_vec());
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skip_non_terminal_tool_call_update() {
        let line =
            br#"{"params":{"update":{"sessionUpdate":"tool_call_update","content":"partial"}}}"#;
        assert!(!keep_updates_line(line));
    }

    #[test]
    fn keep_terminal_completed_failed_is_error() {
        let cases: &[&[u8]] = &[
            br#"{"sessionUpdate":"tool_call_update","status":"completed"}"#,
            br#"{"sessionUpdate":"tool_call_update","status": "completed"}"#,
            br#"{"sessionUpdate":"tool_call_update","status":"failed"}"#,
            br#"{"sessionUpdate":"tool_call_update","status": "failed"}"#,
            br#"{"sessionUpdate":"tool_call_update","isError":true}"#,
            br#"{"sessionUpdate":"tool_call_update","isError": true}"#,
        ];
        for line in cases {
            assert!(
                keep_updates_line(line),
                "expected keep: {}",
                String::from_utf8_lossy(line)
            );
        }
    }

    #[test]
    fn keep_user_message_chunk() {
        let line =
            br#"{"params":{"update":{"sessionUpdate":"user_message_chunk","content":"hi"}}}"#;
        assert!(keep_updates_line(line));
    }

    #[test]
    fn filter_updates_drops_fat_streaming_lines_keeps_two_others() {
        let user = br#"{"sessionUpdate":"user_message_chunk","content":"hi"}"#;
        let fat = format!(
            r#"{{"sessionUpdate":"tool_call_update","content":"{}"}}"#,
            "x".repeat(256)
        );
        let done = br#"{"sessionUpdate":"tool_call_update","status":"completed"}"#;
        let mut data = Vec::new();
        data.extend_from_slice(user);
        data.push(b'\n');
        data.extend_from_slice(fat.as_bytes());
        data.push(b'\n');
        data.extend_from_slice(done);
        data.push(b'\n');
        let kept = filter_updates(&data);
        assert_eq!(kept, vec![user.to_vec(), done.to_vec()]);
    }

    #[test]
    fn filter_updates_strips_cr_and_keeps_incomplete_last_line() {
        let data = b"user_message_chunk\r\ntool_call_update fat\r\nuser_message_chunk again";
        let kept = filter_updates(data);
        assert_eq!(
            kept,
            vec![
                b"user_message_chunk".to_vec(),
                b"user_message_chunk again".to_vec()
            ]
        );
    }

    #[test]
    fn filter_updates_empty_input() {
        assert!(filter_updates(b"").is_empty());
    }
}
