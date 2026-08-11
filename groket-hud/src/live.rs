//! Live-refresh helpers (pure).

use std::collections::{BTreeMap, HashMap, HashSet};

use crate::model::{KindFilter, SchemaField, SessionRow};
use crate::wire::{Overview, SessionMeta, TimelineEvent, TurnsBlock};

pub const LIVE_POLL_MS: u64 = 3000;
pub const IDLE_POLL_MS: u64 = 15_000;
pub const LIVE_TAIL_LIMIT: u32 = 24;
pub const TIMELINE_CHUNK: u32 = 200;
/// Session-row height (padding + title + meta). Rows are uniform; timeline cards are not.
pub const LIST_ROW_H: f32 = 60.0;
/// Collapsed timeline card plus the 12px gap. Used as the unmounted-pad
/// estimate for iced's scrollable. Mounted cards use their real height
/// (titles wrap). Prefer overestimate so we do not skip a card still on screen.
pub const TIMELINE_ROW_H: f32 = 160.0;
/// Extra mounted timeline cards for iced's scrollable (pads keep them off-screen).
pub const TIMELINE_OVERSCAN: usize = 1;
/// Estimated turn card (padding + title + user/assistant + meta).
pub const TURN_ROW_H: f32 = 200.0;
/// Iced's own scrollable uses 60px per wheel line, not a full row.
pub const WHEEL_LINE_PX: f32 = 60.0;
pub const VIRT_OVERSCAN: usize = 4;
/// Minimum scrollbar handle. Iced's own scroller floors at 2px.
pub const SCROLL_HANDLE_MIN: f32 = 24.0;
/// Rail and handle width (iced [`Scrollbar`] default).
pub const SCROLL_RAIL_WIDTH: f32 = 10.0;
/// Handle/track corner radius (iced `scrollable::default`).
pub const SCROLL_RADIUS: f32 = 2.0;

/// Window into a fixed-height virtual list.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct VisibleRange {
    pub start: usize,
    pub end: usize,
    pub pad_top: f32,
    pub pad_bottom: f32,
}

/// Rows to mount for a scroll offset (plus overscan). Empty when *count* is 0.
pub fn visible_range(
    scroll_y: f32,
    viewport_h: f32,
    row_h: f32,
    count: usize,
    overscan: usize,
) -> VisibleRange {
    if count == 0 || row_h <= 0.0 {
        return VisibleRange {
            start: 0,
            end: 0,
            pad_top: 0.0,
            pad_bottom: 0.0,
        };
    }
    let view_h = viewport_h.max(1.0);
    let y = scroll_y.max(0.0);
    let first = (y / row_h).floor() as usize;
    let visible = ((view_h / row_h).ceil() as usize).max(1);
    let start = first.saturating_sub(overscan).min(count);
    let end = (first + visible + overscan).min(count).max(start);
    VisibleRange {
        start,
        end,
        pad_top: start as f32 * row_h,
        pad_bottom: (count.saturating_sub(end)) as f32 * row_h,
    }
}

/// Viewport window for a rail that clips its body (no overscan, no pads).
///
/// Overscan rows stacked in a clipped pane show up as empty slivers.
pub fn rail_visible_range(
    scroll_y: f32,
    viewport_h: f32,
    row_h: f32,
    count: usize,
) -> VisibleRange {
    if count == 0 || row_h <= 0.0 {
        return VisibleRange {
            start: 0,
            end: 0,
            pad_top: 0.0,
            pad_bottom: 0.0,
        };
    }
    let view_h = viewport_h.max(1.0);
    let y = scroll_y.max(0.0);
    let first = (y / row_h).floor() as usize;
    let slots = ((view_h / row_h).ceil() as usize).max(1);
    // Scrolled past a shorter list (catalog page replace / leftover y) must
    // still show the last page — start==count paints an empty rail.
    let start = first.min(count.saturating_sub(slots));
    let end = (start + slots).min(count).max(start);
    VisibleRange {
        start,
        end,
        pad_top: start as f32 * row_h,
        pad_bottom: (count.saturating_sub(end)) as f32 * row_h,
    }
}

/// True when a non-delta ``session/list`` body is a page, not a full snapshot.
pub fn is_partial_list_page(
    incoming_len: usize,
    matched: i64,
    delta: bool,
    incomplete: bool,
    building: bool,
) -> bool {
    if delta {
        return false;
    }
    if incoming_len == 0 {
        return incomplete || building;
    }
    if incomplete || building {
        return matched <= 0 || incoming_len < matched as usize;
    }
    matched > incoming_len as i64
}

/// Like [`visible_range`], but always mounts *cover* (selected row) when in range.
pub fn visible_range_covering(
    scroll_y: f32,
    viewport_h: f32,
    row_h: f32,
    count: usize,
    overscan: usize,
    cover: Option<usize>,
) -> VisibleRange {
    let mut r = visible_range(scroll_y, viewport_h, row_h, count, overscan);
    let Some(i) = cover else {
        return r;
    };
    if i >= count {
        return r;
    }
    if i < r.start {
        r.start = i;
    }
    if i >= r.end {
        r.end = i + 1;
    }
    r.pad_top = r.start as f32 * row_h;
    r.pad_bottom = (count.saturating_sub(r.end)) as f32 * row_h;
    r
}

/// True when *index* is not in the collapsed-height window (ignore covering).
pub fn index_outside_visible(
    scroll_y: f32,
    viewport_h: f32,
    row_h: f32,
    count: usize,
    overscan: usize,
    index: usize,
) -> bool {
    let r = visible_range(scroll_y, viewport_h, row_h, count, overscan);
    index < r.start || index >= r.end
}

/// Thumb offset and length on a rail. `min_handle` keeps the grab usable
/// when `content` is much taller than `viewport` (iced floors at 2px).
pub fn scroller_span(
    content: f32,
    viewport: f32,
    scroll: f32,
    rail: f32,
    min_handle: f32,
) -> (f32, f32) {
    if rail <= 0.0 {
        return (0.0, 0.0);
    }
    if content <= viewport {
        return (0.0, rail);
    }
    let handle = (rail * (viewport / content)).max(min_handle).min(rail);
    let max_scroll = (content - viewport).max(1.0);
    let usable = (rail - handle).max(0.0);
    let t = (scroll.max(0.0) / max_scroll).clamp(0.0, 1.0);
    (usable * t, handle)
}

/// Scroll offset that puts the thumb at `thumb_y` on the rail.
pub fn scroll_from_rail(
    content: f32,
    viewport: f32,
    thumb_y: f32,
    rail: f32,
    min_handle: f32,
) -> f32 {
    let (_, handle) = scroller_span(content, viewport, 0.0, rail, min_handle);
    let max_scroll = (content - viewport).max(0.0);
    let usable = (rail - handle).max(1.0);
    (thumb_y.clamp(0.0, usable) / usable) * max_scroll
}

/// Wheel delta to a clamped content offset (iced scrollable: 60px per line).
pub fn wheel_scroll(delta: iced::mouse::ScrollDelta, scroll: f32, max: f32) -> f32 {
    let dy = match delta {
        iced::mouse::ScrollDelta::Lines { y, .. } => -y * WHEEL_LINE_PX,
        iced::mouse::ScrollDelta::Pixels { y, .. } => -y,
    };
    (scroll + dy).clamp(0.0, max)
}

/// Clamp a rail/wheel offset so the window stays on content.
pub fn clamp_scroll(y: f32, content: f32, viewport: f32) -> f32 {
    y.clamp(0.0, (content - viewport).max(0.0))
}

/// Control `session` argument: live directory path, else id.
pub fn session_rpc_ref(path: &str, session_id: &str) -> String {
    let path = path.trim();
    if !path.is_empty() && std::path::Path::new(path).is_dir() {
        return path.to_string();
    }
    session_id.trim().to_string()
}

/// Timeline pages are fetched only while that tab is showing.
pub fn should_fetch_timeline(on_timeline_tab: bool) -> bool {
    on_timeline_tab
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct TickPlan {
    pub fetch_list: bool,
    pub load_overview: bool,
    pub refresh_timeline: bool,
}

/// Inputs for [`plan_tick`] (notify drain + live poll).
pub struct TickInput<'a> {
    pub notifies: &'a [(String, String)],
    pub selected_sid: &'a str,
    pub overview_sid: &'a str,
    pub palette_live: bool,
    pub list_elapsed_ms: u64,
    pub selected_live: bool,
    pub any_live: bool,
    pub on_timeline: bool,
    pub notes_locked: bool,
}

/// Coalesce notify + poll into at most one list fetch and one overview load.
pub fn plan_tick(input: TickInput<'_>) -> TickPlan {
    let mut plan = TickPlan::default();
    for (method, sid) in input.notifies {
        let mine = !sid.is_empty() && (sid == input.overview_sid || sid == input.selected_sid);
        if method == "session/changed" || method == "session/selected" {
            plan.fetch_list = true;
            if mine {
                plan.load_overview = true;
            }
        }
        if mine
            && (method == "notes/changed" || method == "analysis/changed")
            && !input.notes_locked
        {
            plan.load_overview = true;
        }
    }
    if !input.palette_live {
        return plan;
    }
    let interval = if input.any_live {
        LIVE_POLL_MS
    } else {
        IDLE_POLL_MS
    };
    if input.list_elapsed_ms >= interval {
        plan.fetch_list = true;
        if input.selected_live {
            plan.load_overview = true;
            if input.on_timeline {
                plan.refresh_timeline = true;
            }
        }
    }
    plan
}

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

/// Indices into *events* after kind + typeahead filter.
///
/// Empty *query* keeps timeline order. A non-empty query ranks by
/// [`crate::fuzzy::fzf_score`] and does not clone the events.
pub fn filter_timeline_indices(
    events: &[TimelineEvent],
    kind: KindFilter,
    query: &str,
) -> Vec<usize> {
    let kinded: Vec<usize> = events
        .iter()
        .enumerate()
        .filter(|(_, ev)| ev.matches_kind(kind))
        .map(|(i, _)| i)
        .collect();
    let needle = query.trim();
    if needle.is_empty() {
        return kinded;
    }
    let mut scored: Vec<(i32, usize)> = Vec::new();
    for i in kinded {
        let text = events[i].haystack();
        let score = crate::fuzzy::fzf_score(needle, &text);
        if score > 0 {
            scored.push((score, i));
        }
    }
    scored.sort_unstable_by_key(|b| std::cmp::Reverse(b.0));
    scored.into_iter().map(|(_, i)| i).collect()
}

/// Next ``session/list`` offset, or ``None`` when the catalog drain is done.
pub fn catalog_drain_next(
    offset: u32,
    batch_len: usize,
    page: u32,
    matched: i64,
    stalled: bool,
) -> Option<u32> {
    if stalled || batch_len == 0 || page == 0 {
        return None;
    }
    let next = offset.saturating_add(batch_len as u32);
    if (batch_len as u32) < page {
        return None;
    }
    if matched > 0 && i64::from(next) >= matched {
        return None;
    }
    Some(next)
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

/// Keep paging while the Timeline tab is open and the buffer is short.
pub fn should_continue_timeline(on_timeline: bool, complete: bool, loading: bool) -> bool {
    on_timeline && !complete && !loading
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
pub fn patch_catalog_delta(
    prev: &[SessionRow],
    upserted: Vec<SessionRow>,
    removed: &[String],
) -> Vec<SessionRow> {
    let drop: HashSet<&str> = removed.iter().map(String::as_str).collect();
    let mut kept: Vec<SessionRow> = prev
        .iter()
        .filter(|row| !drop.contains(row.session_id.as_str()))
        .cloned()
        .collect();
    if upserted.is_empty() {
        return kept;
    }
    let patched = merge_catalog_rows(&kept, upserted);
    let mut by_id: HashMap<String, usize> = kept
        .iter()
        .enumerate()
        .map(|(i, row)| (row.session_id.clone(), i))
        .collect();
    for row in patched {
        if let Some(idx) = by_id.get(&row.session_id).copied() {
            kept[idx] = row;
        } else {
            by_id.insert(row.session_id.clone(), kept.len());
            kept.push(row);
        }
    }
    kept
}

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
    fn catalog_delta_removes_and_upserts() {
        use crate::model::SessionRow;
        let prev = vec![
            SessionRow {
                session_id: "a".into(),
                title: "A".into(),
                ..SessionRow::default()
            },
            SessionRow {
                session_id: "b".into(),
                title: "B".into(),
                ..SessionRow::default()
            },
        ];
        let upserted = vec![SessionRow {
            session_id: "c".into(),
            title: "C".into(),
            ..SessionRow::default()
        }];
        let out = patch_catalog_delta(&prev, upserted, &["b".into()]);
        let ids: Vec<&str> = out.iter().map(|r| r.session_id.as_str()).collect();
        assert_eq!(ids, ["a", "c"]);
    }

    #[test]
    fn catalog_pages() {
        assert_eq!(catalog_drain_next(0, 200, 200, 450, false), Some(200));
        assert_eq!(catalog_drain_next(200, 200, 200, 450, false), Some(400));
        assert_eq!(catalog_drain_next(400, 50, 200, 450, false), None);
        assert_eq!(catalog_drain_next(0, 200, 200, 200, false), None);
        assert_eq!(catalog_drain_next(200, 200, 200, 450, true), None);
        assert_eq!(catalog_drain_next(0, 0, 200, 10, false), None);
    }

    #[test]
    fn filter_timeline_indices_keeps_order_without_query() {
        let events = vec![
            ev(0, "user", "hello"),
            ev(1, "tool", "run"),
            ev(2, "agent", "ok"),
        ];
        assert_eq!(
            filter_timeline_indices(&events, KindFilter::All, ""),
            vec![0, 1, 2]
        );
        assert_eq!(
            filter_timeline_indices(&events, KindFilter::Tools, ""),
            vec![1]
        );
    }

    #[test]
    fn filter_timeline_indices_ranks_query_without_cloning_order_source() {
        let events = vec![
            ev(0, "user", "alpha"),
            ev(1, "user", "hud window"),
            ev(2, "user", "other"),
        ];
        assert_eq!(
            filter_timeline_indices(&events, KindFilter::All, "hud"),
            vec![1]
        );
        assert!(filter_timeline_indices(&events, KindFilter::Tools, "hud").is_empty());
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
    fn catalog_refresh_applies_newer_live_status() {
        use crate::model::SessionRow;
        let prev = vec![SessionRow {
            session_id: "s1".into(),
            status: "complete".into(),
            outcome: "success".into(),
            ..SessionRow::default()
        }];
        let next = vec![SessionRow {
            session_id: "s1".into(),
            status: "running".into(),
            outcome: "running".into(),
            ..SessionRow::default()
        }];
        let merged = merge_catalog_rows(&prev, next);
        assert_eq!(merged[0].status, "running");
    }

    #[test]
    fn tick_plan_coalesces_session_changed_into_one_list_fetch() {
        let notifies = vec![
            ("session/changed".into(), "a".into()),
            ("session/changed".into(), "b".into()),
            ("session/changed".into(), "a".into()),
        ];
        let plan = plan_tick(TickInput {
            notifies: &notifies,
            selected_sid: "a",
            overview_sid: "a",
            palette_live: true,
            list_elapsed_ms: 0,
            selected_live: true,
            any_live: true,
            on_timeline: false,
            notes_locked: false,
        });
        assert!(plan.fetch_list);
        assert!(plan.load_overview);
        assert!(!plan.refresh_timeline);
    }

    #[test]
    fn tick_plan_skips_list_fetch_on_quiet_tick() {
        let plan = plan_tick(TickInput {
            notifies: &[],
            selected_sid: "a",
            overview_sid: "a",
            palette_live: true,
            list_elapsed_ms: 500,
            selected_live: true,
            any_live: true,
            on_timeline: false,
            notes_locked: false,
        });
        assert!(!plan.fetch_list);
        assert!(!plan.load_overview);
    }

    #[test]
    fn visible_range_empty_and_first_page() {
        assert_eq!(
            visible_range(0.0, 400.0, 60.0, 0, 3),
            VisibleRange {
                start: 0,
                end: 0,
                pad_top: 0.0,
                pad_bottom: 0.0
            }
        );
        let r = visible_range(0.0, 400.0, 60.0, 200, 3);
        assert_eq!(r.start, 0);
        assert!(r.end <= 12, "end={}", r.end);
        assert!(r.end >= 7);
        assert_eq!(r.pad_top, 0.0);
        assert!(r.pad_bottom > 0.0);
    }

    #[test]
    fn visible_range_clamps_scroll_past_short_buffer() {
        // First timeline page is 120 rows; leftover scroll from a longer list
        // used to panic: events[138..120].
        let r = visible_range(138.0 * 128.0, 400.0, 128.0, 120, 4);
        assert!(r.start <= r.end);
        assert!(r.end <= 120);
        assert_eq!(r.start, 120);
        assert_eq!(r.end, 120);
    }

    #[test]
    fn visible_range_scrolls_with_overscan_and_pads() {
        let r = visible_range(600.0, 400.0, 60.0, 200, 3);
        assert_eq!(r.start, 10 - 3);
        assert_eq!(r.end, 20);
        assert_eq!(r.pad_top, r.start as f32 * 60.0);
        assert_eq!(r.pad_bottom, (200 - r.end) as f32 * 60.0);
    }

    #[test]
    fn index_outside_visible_matches_window() {
        assert!(!index_outside_visible(600.0, 400.0, 60.0, 200, 3, 12));
        assert!(index_outside_visible(600.0, 400.0, 60.0, 200, 3, 5));
        assert!(index_outside_visible(600.0, 400.0, 60.0, 200, 3, 80));
    }

    #[test]
    fn visible_range_covering_keeps_selected_row_without_mounting_all() {
        let r = visible_range_covering(600.0, 400.0, 60.0, 200, 3, Some(5));
        assert_eq!(r.start, 5);
        assert_eq!(r.end, 20);
        assert!(r.end - r.start < 40);
        let inside = visible_range_covering(600.0, 400.0, 60.0, 200, 3, Some(12));
        assert_eq!(inside.start, 7);
        assert_eq!(inside.end, 20);
    }

    #[test]
    fn scroller_keeps_a_usable_handle_on_tall_content() {
        let (y, h) = scroller_span(900.0 * 60.0, 400.0, 0.0, 400.0, SCROLL_HANDLE_MIN);
        assert_eq!(h, SCROLL_HANDLE_MIN);
        assert_eq!(y, 0.0);
        let max_scroll = 900.0 * 60.0 - 400.0;
        let (end, h2) = scroller_span(900.0 * 60.0, 400.0, max_scroll, 400.0, SCROLL_HANDLE_MIN);
        assert_eq!(h2, SCROLL_HANDLE_MIN);
        assert!((end - (400.0 - SCROLL_HANDLE_MIN)).abs() < 0.01);
        let mid = scroll_from_rail(900.0 * 60.0, 400.0, 188.0, 400.0, SCROLL_HANDLE_MIN);
        assert!(mid > 0.0 && mid < max_scroll);
        let (y0, full) = scroller_span(100.0, 400.0, 0.0, 400.0, SCROLL_HANDLE_MIN);
        assert_eq!(y0, 0.0);
        assert_eq!(full, 400.0);
        assert_eq!(
            scroller_span(100.0, 50.0, 0.0, 0.0, SCROLL_HANDLE_MIN),
            (0.0, 0.0)
        );
        assert_eq!(
            scroll_from_rail(100.0, 400.0, 10.0, 400.0, SCROLL_HANDLE_MIN),
            0.0
        );
    }

    #[test]
    fn clamp_scroll_keeps_offset_on_content() {
        assert_eq!(clamp_scroll(-10.0, 600.0, 400.0), 0.0);
        assert_eq!(clamp_scroll(50.0, 600.0, 400.0), 50.0);
        assert_eq!(clamp_scroll(500.0, 600.0, 400.0), 200.0);
        assert_eq!(clamp_scroll(50.0, 100.0, 400.0), 0.0);
    }

    #[test]
    fn rail_window_does_not_need_pad_spacers() {
        // Pads sized the full list for iced's scrollable. A rail list that
        // inserts pad_top as a widget shows an empty viewport when scrolled.
        let r = visible_range(600.0, 400.0, 60.0, 200, 3);
        assert!(r.pad_top > 0.0);
        assert!(r.start > 0);
        assert!(r.end - r.start < 40);
    }

    #[test]
    fn rail_visible_range_is_only_the_viewport() {
        let r = rail_visible_range(0.0, 400.0, 60.0, 200);
        assert_eq!(r.start, 0);
        assert_eq!(r.end, 7);
        let mid = rail_visible_range(600.0, 400.0, 60.0, 200);
        assert_eq!(mid.start, 10);
        assert_eq!(mid.end, 17);
        let tall = rail_visible_range(0.0, 400.0, 160.0, 50);
        assert_eq!(tall.start, 0);
        assert_eq!(tall.end, 3);
        assert!(tall.end <= 3);
        let past = rail_visible_range(200.0 * 160.0, 400.0, 160.0, 20);
        assert!(past.start < past.end, "{past:?}");
        assert_eq!(past.end, 20);
        assert!(past.start <= 17, "start={}", past.start);
    }

    #[test]
    fn partial_list_page_is_not_a_full_snapshot() {
        assert!(is_partial_list_page(0, 0, false, true, true));
        assert!(is_partial_list_page(1, 964, false, false, false));
        assert!(is_partial_list_page(200, 964, false, false, false));
        assert!(!is_partial_list_page(964, 964, false, false, false));
        assert!(!is_partial_list_page(1, 1, true, false, false));
        assert!(!is_partial_list_page(0, 0, false, false, false));
        assert!(!is_partial_list_page(0, 0, false, false, false));
    }

    #[test]
    fn wheel_scroll_matches_iced_line_step() {
        let d = iced::mouse::ScrollDelta::Lines { x: 0.0, y: -1.0 };
        assert_eq!(wheel_scroll(d, 0.0, 600.0), 60.0);
        let up = iced::mouse::ScrollDelta::Lines { x: 0.0, y: 3.0 };
        assert_eq!(wheel_scroll(up, 40.0, 600.0), 0.0);
        let px = iced::mouse::ScrollDelta::Pixels { x: 0.0, y: -20.0 };
        assert_eq!(wheel_scroll(px, 0.0, 600.0), 20.0);
        assert_eq!(
            wheel_scroll(
                iced::mouse::ScrollDelta::Lines { x: 0.0, y: -10.0 },
                500.0,
                600.0
            ),
            600.0
        );
    }

    #[test]
    fn session_rpc_ref_uses_path_only_when_directory_exists() {
        let dir = std::env::temp_dir().join("groket-hud-rpc-ref");
        let _ = std::fs::create_dir_all(&dir);
        assert_eq!(
            session_rpc_ref(dir.to_str().unwrap(), "uuid"),
            dir.to_str().unwrap()
        );
        assert_eq!(
            session_rpc_ref("/no/such/groket-hud-session", "uuid"),
            "uuid"
        );
        assert_eq!(session_rpc_ref("", "uuid"), "uuid");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn should_fetch_timeline_only_on_that_tab() {
        assert!(should_fetch_timeline(true));
        assert!(!should_fetch_timeline(false));
    }

    #[test]
    fn should_continue_timeline_while_short() {
        assert!(should_continue_timeline(true, false, false));
        assert!(!should_continue_timeline(true, true, false));
        assert!(!should_continue_timeline(true, false, true));
        assert!(!should_continue_timeline(false, false, false));
    }

    #[test]
    fn tick_plan_idle_poll_refreshes_list() {
        let plan = plan_tick(TickInput {
            notifies: &[],
            selected_sid: "a",
            overview_sid: "a",
            palette_live: true,
            list_elapsed_ms: IDLE_POLL_MS,
            selected_live: false,
            any_live: false,
            on_timeline: false,
            notes_locked: false,
        });
        assert!(plan.fetch_list);
        assert!(!plan.load_overview);
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
