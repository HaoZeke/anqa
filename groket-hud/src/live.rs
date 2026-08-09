//! Live-refresh helpers (pure).

use std::collections::{BTreeMap, HashMap, HashSet};

use crate::model::{SchemaField, SessionRow};
use crate::wire::{Overview, SessionMeta, TimelineEvent, TurnsBlock};

pub const LIVE_POLL_MS: u64 = 3000;
pub const IDLE_POLL_MS: u64 = 15_000;
pub const LIVE_TAIL_LIMIT: u32 = 24;
pub const TIMELINE_CHUNK: u32 = 200;

const LIVE_STATUS: &[&str] = &[
    "running",
    "ending",
    "in_progress",
    "pending",
    "awaiting",
    "awaiting_follow_up",
];

pub fn is_live_status(status: &str) -> bool {
    let x = status
        .trim()
        .to_ascii_lowercase()
        .replace(char::is_whitespace, "_");
    if x.is_empty() || x == "—" || x == "-" {
        return false;
    }
    if LIVE_STATUS.contains(&x.as_str()) {
        return true;
    }
    x.contains("await") || x == "run" || x.starts_with("runn")
}

pub fn has_open_turn(turns: &TurnsBlock) -> bool {
    turns.has_open_turn()
}

pub fn session_needs_live_poll(status: &str, turns: Option<&TurnsBlock>) -> bool {
    is_live_status(status) || turns.is_some_and(has_open_turn)
}

pub fn timeline_seek_offset(focus_index: i64, pad: i64) -> u32 {
    if focus_index < 0 {
        return 0;
    }
    (focus_index - pad.max(0)).max(0) as u32
}

pub fn timeline_coverage_complete(buffered: usize, total: u32) -> bool {
    if total == 0 {
        return buffered == 0;
    }
    buffered >= total as usize
}

pub fn timeline_first_missing_offset(events: &[TimelineEvent], total: u32) -> u32 {
    if total == 0 {
        return 0;
    }
    let mut have = HashSet::new();
    for ev in events {
        have.insert(ev.index);
    }
    for i in 0..i64::from(total) {
        if !have.contains(&i) {
            return i as u32;
        }
    }
    total
}

pub struct MergeResult {
    pub events: Vec<TimelineEvent>,
    pub added: usize,
    pub updated: usize,
}

pub fn merge_timeline_by_index(existing: &[TimelineEvent], batch: &[TimelineEvent]) -> MergeResult {
    let mut by_index: BTreeMap<i64, TimelineEvent> = BTreeMap::new();
    for ev in existing {
        by_index.insert(ev.index, ev.clone());
    }
    let mut added = 0;
    let mut updated = 0;
    for ev in batch {
        match by_index.get(&ev.index) {
            Some(prev) if prev.fingerprint() == ev.fingerprint() => {}
            Some(_) => {
                by_index.insert(ev.index, ev.clone());
                updated += 1;
            }
            None => {
                by_index.insert(ev.index, ev.clone());
                added += 1;
            }
        }
    }
    MergeResult {
        events: by_index.into_values().collect(),
        added,
        updated,
    }
}

pub fn is_soft_notes_save_error(msg: &str) -> bool {
    let m = msg;
    if m.is_empty() {
        return false;
    }
    let low = m.to_ascii_lowercase();
    low.contains("operator notes changed")
        || low.contains("notes_conflict")
        || low.contains("409")
        || low.contains("notes conflict")
        || (low.contains("stale") && low.contains("revision"))
        || low.contains("expectedrevision")
        || low.contains("note.id")
        || low.contains("must match")
        || low.contains("note is required")
        || low.contains("noteid is required")
}

pub fn default_notes_schema() -> Vec<SchemaField> {
    vec![
        SchemaField {
            id: "summary".into(),
            label: "Summary".into(),
            choices: vec![],
            pick: "one-of".into(),
        },
        SchemaField {
            id: "detail".into(),
            label: "Detail".into(),
            choices: vec![],
            pick: "one-of".into(),
        },
    ]
}

pub fn notes_schema_fields(overview: Option<&Overview>) -> Vec<SchemaField> {
    let Some(ov) = overview else {
        return default_notes_schema();
    };
    let mut out = Vec::new();
    for f in &ov.notes.schema.fields {
        let id = f.id.trim();
        if id.is_empty() {
            continue;
        }
        let label = if f.label.is_empty() {
            id.to_string()
        } else {
            f.label.clone()
        };
        out.push(SchemaField {
            id: id.to_string(),
            label,
            choices: f.choices.clone(),
            pick: if f.pick.is_empty() {
                "one-of".into()
            } else {
                f.pick.clone()
            },
        });
    }
    if out.is_empty() {
        default_notes_schema()
    } else {
        out
    }
}

#[derive(Debug, Clone, Default)]
pub struct CardMark {
    pub findings: u32,
    pub notes: u32,
    pub errors: u32,
    pub first_finding_event: Option<i64>,
    pub first_note_id: String,
}

pub fn card_marks_from_overview(
    overview: &Overview,
) -> (HashMap<i64, CardMark>, HashMap<i64, CardMark>) {
    let mut turns: HashMap<i64, CardMark> = HashMap::new();
    let mut events: HashMap<i64, CardMark> = HashMap::new();

    for f in &overview.findings.findings {
        let evs = &f.event_indices;
        let primary = f.primary_event_index.or_else(|| evs.first().copied());
        for ti in &f.turn_indices {
            let row = turns.entry(*ti).or_default();
            row.findings += 1;
            if row.first_finding_event.is_none() {
                row.first_finding_event = primary;
            }
        }
        for ei in evs {
            let row = events.entry(*ei).or_default();
            row.findings += 1;
            if row.first_finding_event.is_none() {
                row.first_finding_event = primary;
            }
        }
        if let Some(first) = primary {
            if evs.is_empty() {
                let row = events.entry(first).or_default();
                row.findings += 1;
                if row.first_finding_event.is_none() {
                    row.first_finding_event = Some(first);
                }
            }
        }
    }

    for n in &overview.notes.notes {
        let nid = n.id.clone();
        if let Some(ti) = n.turn_index {
            let row = turns.entry(ti).or_default();
            row.notes += 1;
            if row.first_note_id.is_empty() {
                row.first_note_id = nid.clone();
            }
        }
        for ei in &n.event_indices {
            let row = events.entry(*ei).or_default();
            row.notes += 1;
            if row.first_note_id.is_empty() {
                row.first_note_id = nid.clone();
            }
        }
    }

    for t in &overview.turns.turns {
        let err = t.tool_error_count;
        if err == 0 {
            continue;
        }
        turns.entry(t.turn_index).or_default().errors += err as u32;
    }

    (turns, events)
}

/// Keep overview-patched status when a quiet catalog refresh sends a blank label.
pub fn merge_catalog_rows(prev: &[SessionRow], next: Vec<SessionRow>) -> Vec<SessionRow> {
    use crate::format::{is_blank_status, list_status_label};

    let old: HashMap<&str, &SessionRow> = prev.iter().map(|r| (r.session_id.as_str(), r)).collect();
    next.into_iter()
        .map(|mut row| {
            if let Some(p) = old.get(row.session_id.as_str()) {
                if is_blank_status(&row.status) && !is_blank_status(&p.status) {
                    row.status = p.status.clone();
                }
                if row.outcome.is_empty() && !p.outcome.is_empty() {
                    row.outcome = p.outcome.clone();
                }
                if row.context_usage_compact.is_empty() && !p.context_usage_compact.is_empty() {
                    row.context_usage_compact = p.context_usage_compact.clone();
                }
            }
            row.status = list_status_label(&row.status, &row.outcome);
            row
        })
        .collect()
}

pub fn patch_list_row_from_meta(rows: &mut [SessionRow], session_id: &str, meta: &SessionMeta) {
    let Some(row) = rows.iter_mut().find(|r| r.session_id == session_id) else {
        return;
    };
    if !meta.status.is_empty() {
        row.status = crate::format::list_status_label(&meta.status, &row.outcome);
    }
    if !meta.title.is_empty() {
        row.title = meta.title.clone();
    }
    if !meta.label.is_empty() {
        row.label = meta.label.clone();
    }
    if !meta.model.is_empty() {
        row.model = meta.model.clone();
    }
    if !meta.outcome.is_empty() {
        row.outcome = meta.outcome.clone();
        row.status = crate::format::list_status_label(&row.status, &row.outcome);
    }
    if !meta.context_usage_compact.is_empty() {
        row.context_usage_compact = meta.context_usage_compact.clone();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::{TimelineEvent, TurnRow, TurnsBlock};

    fn ev(index: i64, kind: &str, content: &str) -> TimelineEvent {
        TimelineEvent {
            index,
            kind: kind.into(),
            content: content.into(),
            ..TimelineEvent::default()
        }
    }

    #[test]
    fn live_status_labels() {
        assert!(is_live_status("running"));
        assert!(is_live_status("awaiting"));
        assert!(is_live_status("awaiting_follow_up"));
        assert!(is_live_status("ending"));
        assert!(!is_live_status("complete"));
        assert!(!is_live_status("cancelled"));
        assert!(!is_live_status("—"));
        assert!(!is_live_status(""));
    }

    #[test]
    fn open_turn_forces_poll() {
        let open = TurnsBlock {
            turns: vec![TurnRow {
                open: true,
                ..TurnRow::default()
            }],
            ..TurnsBlock::default()
        };
        let closed = TurnsBlock {
            turns: vec![TurnRow {
                open: false,
                ..TurnRow::default()
            }],
            ..TurnsBlock::default()
        };
        assert!(session_needs_live_poll("complete", Some(&open)));
        assert!(!session_needs_live_poll("complete", Some(&closed)));
    }

    #[test]
    fn timeline_holes() {
        assert!(!timeline_coverage_complete(5, 100));
        assert!(timeline_coverage_complete(100, 100));
        assert!(timeline_coverage_complete(0, 0));
        let events = vec![ev(0, "user", ""), ev(2, "agent", "")];
        assert_eq!(timeline_first_missing_offset(&events, 3), 1);
    }

    #[test]
    fn merge_updates_changed_only() {
        let existing = vec![ev(1, "agent", "a")];
        let batch = vec![ev(1, "agent", "a"), ev(2, "user", "b")];
        let m = merge_timeline_by_index(&existing, &batch);
        assert_eq!(m.added, 1);
        assert_eq!(m.updated, 0);
        assert_eq!(m.events.len(), 2);
    }

    #[test]
    fn soft_notes_errors() {
        assert!(is_soft_notes_save_error("operator notes changed"));
        assert!(is_soft_notes_save_error("notes_conflict"));
        assert!(is_soft_notes_save_error(
            "RPC error 409: operator notes changed"
        ));
        assert!(!is_soft_notes_save_error("connection refused"));
        assert!(!is_soft_notes_save_error(""));
    }

    #[test]
    fn catalog_refresh_keeps_overview_status() {
        use crate::model::SessionRow;
        let prev = vec![SessionRow {
            session_id: "s1".into(),
            status: "complete".into(),
            outcome: "success".into(),
            context_usage_compact: "12%".into(),
            ..SessionRow::default()
        }];
        let next = vec![SessionRow {
            session_id: "s1".into(),
            status: "—".into(),
            ..SessionRow::default()
        }];
        let merged = merge_catalog_rows(&prev, next);
        assert_eq!(merged[0].status, "complete");
        assert_eq!(merged[0].outcome, "success");
        assert_eq!(merged[0].context_usage_compact, "12%");
    }

    #[test]
    fn schema_fallback() {
        let fields = notes_schema_fields(None);
        assert_eq!(fields.len(), 2);
        assert_eq!(fields[0].id, "summary");
    }

    #[test]
    fn card_marks_and_meta_patch_from_typed_overview() {
        let ov = crate::wire::decode_overview(&{
            let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/overview.json");
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
        })
        .unwrap();
        let (turns, _events) = card_marks_from_overview(&ov);
        assert!(turns.is_empty() || ov.findings.findings.is_empty());
        let fields = notes_schema_fields(Some(&ov));
        assert!(fields.iter().any(|f| f.id == "summary"));

        let mut rows = vec![SessionRow {
            session_id: "sess-wire".into(),
            status: "—".into(),
            ..SessionRow::default()
        }];
        patch_list_row_from_meta(&mut rows, "sess-wire", &ov.meta);
        assert_eq!(rows[0].status, "running");
        assert_eq!(rows[0].title, "View session");
    }
}
