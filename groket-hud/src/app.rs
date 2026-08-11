//! iced application: state, RPC, hotkey, live poll.

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use global_hotkey::{GlobalHotKeyEvent, GlobalHotKeyManager, HotKeyState};
use iced::keyboard::{key::Named, Key, Modifiers as KeyMods};
use iced::widget::operation::{self, AbsoluteOffset};
use iced::widget::Id;
use iced::window::{self, Mode};
use iced::{
    event, keyboard, time, Element, Event, Pixels, Point, Settings, Size, Subscription, Task, Theme,
};
use serde_json::{json, Value};

use crate::control::{self, ControlError};
use crate::format::{
    control_down_message, event_body_text, extract_event, list_status_label, new_note_id,
};
use crate::fuzzy::fuzzy_filter_indices;
use crate::live::{
    card_marks_from_overview, clamp_scroll, context_fraction, filter_timeline_indices,
    first_list_fetch, index_outside_visible, is_partial_list_page, is_soft_notes_save_error,
    merge_catalog_rows, merge_timeline_by_index, next_list_offset, notes_schema_fields,
    patch_catalog_delta, patch_list_row_from_meta, plan_tick, previous_timeline_page,
    scroll_after_prepend, session_card_height, session_list_content_height,
    session_needs_live_poll, session_row_meta, session_rpc_ref, should_fetch_timeline,
    should_load_previous_timeline, timeline_coverage_complete, timeline_page_next,
    timeline_range_label, timeline_window_start, toggle_expand_set, trim_timeline_buffer, CardMark,
    TickInput, IDLE_POLL_MS, LIVE_POLL_MS, LIVE_TAIL_LIMIT, TIMELINE_BUFFER_CAP, TIMELINE_CHUNK,
    TIMELINE_OPEN_CHARS, TIMELINE_OVERSCAN, TIMELINE_PREVIEW_CHARS, TIMELINE_ROW_H, TURN_ROW_H,
};
use crate::model::{KindFilter, NoteDraft, SchemaField, SessionRow, Tab};
use crate::place;
use crate::prefs;
use crate::shortcut;
use crate::theme;
use crate::view;
use crate::wire::{
    decode_overview, decode_session_list, decode_session_list_response, decode_timeline_page,
    NotesBlock, Overview, TimelineEvent,
};

const HUD_W: f32 = 780.0;
const HUD_H: f32 = 560.0;

#[derive(Debug, Clone)]
pub enum Message {
    SearchChanged(String),
    SelectSession(usize),
    SetTab(Tab),
    TimelineQuery(String),
    TimelineKind(KindFilter),
    JumpTimeline(i64),
    SelectTimeline(i64),
    TurnExpand {
        turn: i64,
        open: bool,
    },
    LoadMoreTimeline,
    StartNote {
        turn: String,
        event: String,
    },
    OpenNote(String),
    ResetDraft,
    NoteField {
        id: String,
        value: String,
    },
    NoteTurn(String),
    SaveNote,
    RequestDelete(String),
    PopOutWindow,
    Hotkey,
    Tick,
    FocusSearch(u8),
    RawEvent(Event),
    Inited(Result<String, String>),
    ListLoaded {
        quiet: bool,
        result: Result<Value, String>,
    },
    ListPage {
        offset: u32,
        result: Result<Value, String>,
    },
    OverviewLoaded {
        gen: u64,
        sid: String,
        quiet: bool,
        result: Result<Value, String>,
    },
    TimelineLoaded {
        gen: u64,
        sid: String,
        offset: u32,
        append: bool,
        advance: bool,
        result: Result<Value, String>,
    },
    NoteSaved(Result<Value, String>),
    NoteDeleted {
        id: String,
        result: Result<Value, String>,
    },
    WindowId(Option<window::Id>),
    WindowPos(Option<Point>),
    X11Focus {
        xid: u64,
        attempt: u8,
    },
    Hide,
    Tray(crate::tray::TrayAction),
    MdLink(String),
    ListScroll(icedtea::collection::VisibleWindow),
    TimelineScroll {
        y: f32,
        height: f32,
    },
    TurnScroll {
        y: f32,
        height: f32,
    },
    TimelineSearchApply(u64),
    FindingExpand {
        id: String,
        open: bool,
    },
    NoteExpand {
        id: String,
        open: bool,
    },
    FollowDraft(String),
    SendFollow,
    MarkDone,
    CopyPath,
    CopyText(String),
    ExtractAction {
        key: ExtractKey,
        action: iced::widget::text_editor::Action,
    },
    ToastDismiss(u64),
    FollowDone(Result<Value, String>),
}

/// One selectable body buffer (expanded event or turn text).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ExtractKey {
    Event(i64),
    TurnUser(i64),
    TurnAsst(i64),
    Overview(&'static str),
}

pub struct Hud {
    query: String,
    all_sessions: Vec<SessionRow>,
    sessions: Vec<SessionRow>,
    active: usize,
    tab: Tab,
    overview: Option<Overview>,
    overview_sid: String,
    overview_pending: String,
    overview_gen: u64,
    timeline: Vec<TimelineEvent>,
    timeline_sid: String,
    timeline_total: u32,
    timeline_offset: u32,
    timeline_next: u32,
    timeline_gen: u64,
    timeline_loading: bool,
    timeline_query: String,
    timeline_query_draft: String,
    timeline_search_pending: bool,
    timeline_kind: KindFilter,
    timeline_focus: Option<i64>,
    timeline_expanded: HashSet<i64>,
    note_draft: NoteDraft,
    note_compose_lock: bool,
    note_saving: bool,
    note_delete_armed: String,
    note_delete_until: Option<Instant>,
    status: String,
    status_err: bool,
    hotkey_hint: String,
    window_mode: bool,
    visible: bool,
    palette_live: bool,
    palette_origin: Option<Point>,
    last_live: Instant,
    typing_notes: bool,
    search_id: Id,
    tl_search_id: Id,
    theme_name: String,
    _hotkeys: Option<GlobalHotKeyManager>,
    _tray: Option<crate::tray::HudTray>,
    notify_q: Arc<Mutex<VecDeque<(String, Value)>>>,
    window_id: Option<window::Id>,
    catalog_revision: i64,
    list_window: icedtea::collection::VisibleWindow,
    list_scroll_id: Id,
    session_metas: Vec<String>,
    tl_scroll_y: f32,
    tl_view_h: f32,
    tl_scroll_id: Id,
    turn_scroll_y: f32,
    turn_view_h: f32,
    tl_filter: Vec<usize>,
    turn_marks: std::collections::HashMap<i64, CardMark>,
    event_marks: std::collections::HashMap<i64, CardMark>,
    seen_status: std::collections::HashMap<String, String>,
    seen_analysis: std::collections::HashMap<String, String>,
    toasts: icedtea::toast::ToastQueue,
    last_tick: Instant,
    spin_phase: f32,
    catalog_busy: bool,
    findings_open: HashSet<String>,
    notes_open: HashSet<String>,
    turns_open: HashSet<i64>,
    follow_draft: String,
    timeline_search_gen: u64,
    extracts: HashMap<ExtractKey, iced::widget::text_editor::Content>,
    extract_src: HashMap<ExtractKey, String>,
}

impl Default for Hud {
    fn default() -> Self {
        Self {
            query: String::new(),
            all_sessions: vec![],
            sessions: vec![],
            active: 0,
            tab: Tab::Overview,
            overview: None,
            overview_sid: String::new(),
            overview_pending: String::new(),
            overview_gen: 0,
            timeline: vec![],
            timeline_sid: String::new(),
            timeline_total: 0,
            timeline_offset: 0,
            timeline_next: 0,
            timeline_gen: 0,
            timeline_loading: false,
            timeline_query: String::new(),
            timeline_query_draft: String::new(),
            timeline_search_pending: false,
            timeline_kind: KindFilter::All,
            timeline_focus: None,
            timeline_expanded: HashSet::new(),
            note_draft: NoteDraft::default(),
            note_compose_lock: false,
            note_saving: false,
            note_delete_armed: String::new(),
            note_delete_until: None,
            status: "connecting…".into(),
            status_err: false,
            hotkey_hint: shortcut::default_shortcut_label().into(),
            window_mode: std::env::var_os("GROKET_HUD_WINDOW").is_some(),
            visible: true,
            palette_live: true,
            palette_origin: None,
            last_live: Instant::now(),
            typing_notes: false,
            search_id: Id::new("search"),
            tl_search_id: Id::new("tl-search"),
            theme_name: prefs::theme_name(),
            _hotkeys: None,
            _tray: None,
            notify_q: Arc::new(Mutex::new(VecDeque::new())),
            catalog_revision: 0,
            window_id: None,
            list_window: icedtea::collection::VisibleWindow::new(400.0),
            list_scroll_id: Id::new("hud-sessions"),
            session_metas: vec![],
            tl_scroll_y: 0.0,
            tl_view_h: 400.0,
            tl_scroll_id: Id::new("hud-timeline"),
            turn_scroll_y: 0.0,
            turn_view_h: 400.0,
            tl_filter: vec![],
            turn_marks: std::collections::HashMap::new(),
            event_marks: std::collections::HashMap::new(),
            seen_status: std::collections::HashMap::new(),
            seen_analysis: std::collections::HashMap::new(),
            toasts: icedtea::toast::ToastQueue::new(),
            last_tick: Instant::now(),
            spin_phase: 0.0,
            catalog_busy: false,
            findings_open: HashSet::new(),
            notes_open: HashSet::new(),
            turns_open: HashSet::new(),
            follow_draft: String::new(),
            timeline_search_gen: 0,
            extracts: HashMap::new(),
            extract_src: HashMap::new(),
        }
    }
}

fn linux_app_id(win: &mut window::Settings) {
    #[cfg(target_os = "linux")]
    {
        win.platform_specific.application_id = "dev.indynull.groket-hud".into();
    }
    let _ = win;
}

fn with_hud_icon(mut win: window::Settings) -> window::Settings {
    win.icon = crate::brand::window_icon();
    win
}

/// Overlay is already the mapped palette: do not remap, resize, or refetch.
pub fn overlay_already_mapped(visible: bool, window_mode: bool, has_window: bool) -> bool {
    visible && !window_mode && has_window
}

pub fn palette_window_settings() -> window::Settings {
    let mut win = with_hud_icon(window::Settings {
        size: Size::new(HUD_W, HUD_H),
        position: window::Position::Centered,
        min_size: Some(Size::new(HUD_W, HUD_H)),
        max_size: Some(Size::new(HUD_W, HUD_H)),
        resizable: false,
        decorations: false,
        visible: true,
        level: window::Level::AlwaysOnTop,
        exit_on_close_request: false,
        ..window::Settings::default()
    });
    linux_app_id(&mut win);
    #[cfg(target_os = "linux")]
    {
        // X11: unmapped by the tiler so the overlay stays a 780x560 card.
        win.platform_specific.override_redirect = true;
    }
    win
}

pub fn app_window_settings() -> window::Settings {
    let mut win = with_hud_icon(window::Settings {
        size: Size::new(980.0, 700.0),
        position: window::Position::Default,
        min_size: Some(Size::new(640.0, 440.0)),
        max_size: None,
        resizable: true,
        decorations: true,
        visible: true,
        level: window::Level::Normal,
        exit_on_close_request: false,
        ..window::Settings::default()
    });
    linux_app_id(&mut win);
    #[cfg(target_os = "linux")]
    {
        // Normal client: tiling WMs insert this; stacking WMs just map it.
        win.platform_specific.override_redirect = false;
    }
    win
}

pub fn run() -> iced::Result {
    crate::log::info(&format!("hud start log={}", crate::log::path().display()));
    iced::daemon(Hud::new, Hud::update, Hud::view)
        .title("groket")
        .subscription(Hud::subscription)
        .theme(|hud: &Hud, window| Some(hud.theme(window)))
        .settings(Settings {
            id: Some("dev.indynull.groket-hud".into()),
            antialiasing: true,
            vsync: true,
            default_text_size: Pixels::from(crate::typo::BODY),
            default_font: crate::typo::UI,
            fonts: vec![
                std::borrow::Cow::Borrowed(crate::typo::UI_BYTES),
                std::borrow::Cow::Borrowed(crate::typo::MONO_BYTES),
            ],
        })
        .run()
}

#[cfg(target_os = "macos")]
pub fn set_macos_accessory() {
    crate::macoswin::set_accessory_policy();
}

#[cfg(not(target_os = "macos"))]
pub fn set_macos_accessory() {}

impl Hud {
    fn new() -> (Self, Task<Message>) {
        let mut hud = Hud::default();
        let (hk, label) = shortcut::resolve_summon_shortcut();
        hud.hotkey_hint = label.clone();
        let skip_hotkey = hud.window_mode;
        if !skip_hotkey {
            match GlobalHotKeyManager::new() {
                Ok(mgr) => {
                    if let Err(err) = mgr.register(hk) {
                        let msg = format!("failed to register shortcut {label}: {err}");
                        crate::log::error(&msg);
                        eprintln!("groket-hud: {msg}");
                    } else {
                        eprintln!("groket-hud: summon shortcut {label}");
                    }
                    hud._hotkeys = Some(mgr);
                }
                Err(err) => {
                    let msg = format!("global hotkey unavailable: {err}");
                    crate::log::error(&msg);
                    eprintln!("groket-hud: {msg}");
                }
            }
        }
        match crate::tray::install() {
            Ok(tray) => {
                eprintln!("groket-hud: tray ready");
                hud._tray = Some(tray);
            }
            Err(err) => {
                let msg = format!("tray: {err}");
                crate::log::error(&msg);
                eprintln!("groket-hud: {msg}");
            }
        }
        let q = hud.notify_q.clone();
        let _ = control::spawn_notify_listener(move |method, params| {
            if let Ok(mut g) = q.lock() {
                g.push_back((method, params));
                if g.len() > 64 {
                    g.pop_front();
                }
            }
        });
        let (id, open) = window::open(if hud.window_mode {
            app_window_settings()
        } else {
            palette_window_settings()
        });
        hud.window_id = Some(id);
        let mut boot = vec![
            open.map(|id| Message::WindowId(Some(id))),
            Task::perform(rpc(control::initialize), |r| {
                Message::Inited(r.map(|_| String::new()))
            }),
            fetch_list(false, 0),
        ];
        if crate::tray::show_on_start() {
            boot.push(hud.show_palette());
        }
        (hud, Task::batch(boot))
    }

    fn theme(&self, _window: window::Id) -> Theme {
        theme::iced_theme(&self.theme_name)
    }

    fn subscription(&self) -> Subscription<Message> {
        let mut subs = vec![
            event::listen_with(interesting_hud_event),
            hotkey_subscription(),
            notify_subscription(),
        ];
        if self.visible {
            let any_live = session_needs_live_poll(
                &self.selected_status(),
                self.overview.as_ref().map(|o| &o.turns),
            ) || self
                .all_sessions
                .iter()
                .any(|r| session_needs_live_poll(&r.status, None));
            let poll = if any_live { LIVE_POLL_MS } else { IDLE_POLL_MS };
            subs.push(time::every(Duration::from_millis(poll)).map(|_| Message::Tick));
        }
        if self.note_delete_until.is_some() {
            subs.push(time::every(Duration::from_millis(250)).map(|_| Message::Tick));
        }
        if self._tray.is_some() {
            subs.push(tray_subscription());
        }
        Subscription::batch(subs)
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::SearchChanged(q) => {
                self.query = q;
                self.rerank_visible();
                Task::none()
            }
            Message::SelectSession(i) => {
                if i >= self.sessions().len() {
                    return self.focus_overlay();
                }
                let same = self.active == i && self.overview.is_some();
                self.active = i;
                if same {
                    return self.focus_overlay();
                }
                self.reset_detail_chrome();
                let sid = self.sessions()[i].session_id.clone();
                // Parse once (owner single-flight) and have the first timeline
                // page ready before the operator opens that tab.
                Task::batch([
                    self.load_overview(false),
                    self.ensure_timeline(sid, false),
                    self.focus_overlay(),
                ])
            }
            Message::SetTab(tab) => {
                if self.tab != tab {
                    self.tl_scroll_y = 0.0;
                }
                self.tab = tab;
                let load = if tab == Tab::Timeline {
                    if let Some(sid) = self.selected_sid() {
                        self.ensure_timeline(sid, false)
                    } else {
                        Task::none()
                    }
                } else {
                    Task::none()
                };
                Task::batch([load, self.focus_overlay()])
            }
            Message::TimelineQuery(q) => {
                self.timeline_query_draft = q;
                self.timeline_focus = None;
                self.timeline_expanded.clear();
                self.timeline_search_gen = self.timeline_search_gen.wrapping_add(1);
                // Hold the last applied page until debounce. Bump gen so an
                // in-flight fill cannot merge a new-query slice onto it.
                self.timeline_gen = self.timeline_gen.wrapping_add(1);
                self.timeline_search_pending = true;
                self.timeline_loading = true;
                let gen = self.timeline_search_gen;
                Task::perform(
                    async {
                        tokio::time::sleep(Duration::from_millis(280)).await;
                    },
                    move |()| Message::TimelineSearchApply(gen),
                )
            }
            Message::TimelineSearchApply(gen) => {
                if gen != self.timeline_search_gen {
                    return Task::none();
                }
                self.timeline_query = self.timeline_query_draft.clone();
                self.timeline_search_pending = false;
                self.timeline_loading = false;
                if let Some(sid) = self.selected_sid() {
                    return self.ensure_timeline(sid, true);
                }
                Task::none()
            }
            Message::TimelineKind(k) => {
                self.timeline_kind = k;
                self.timeline_focus = None;
                self.timeline_expanded.clear();
                if let Some(sid) = self.selected_sid() {
                    return self.ensure_timeline(sid, true);
                }
                Task::none()
            }
            Message::JumpTimeline(ix) => self.jump_timeline(ix),
            Message::SelectTimeline(ix) => {
                toggle_expand_set(&mut self.timeline_expanded, ix);
                self.timeline_focus = if self.timeline_expanded.contains(&ix) {
                    Some(ix)
                } else {
                    None
                };
                if self.timeline_expanded.contains(&ix) {
                    self.bind_event_extract(ix);
                    return self.fetch_open_event(ix);
                }
                Task::none()
            }
            Message::TurnExpand { turn, open } => {
                if open {
                    self.turns_open.insert(turn);
                } else {
                    self.turns_open.remove(&turn);
                }
                Task::none()
            }
            Message::LoadMoreTimeline => self.load_more_timeline(),
            Message::StartNote { turn, event } => {
                self.note_draft = NoteDraft {
                    id: String::new(),
                    turn_index: turn,
                    event_index: event,
                    fields: vec![],
                };
                self.note_compose_lock = true;
                self.typing_notes = true;
                self.tab = Tab::Notes;
                Task::none()
            }
            Message::OpenNote(nid) => {
                self.open_note(&nid);
                Task::none()
            }
            Message::ResetDraft => {
                self.note_draft = NoteDraft::default();
                self.note_compose_lock = false;
                self.typing_notes = false;
                self.note_saving = false;
                Task::none()
            }
            Message::NoteField { id, value } => {
                self.note_draft.set_field(&id, value);
                self.note_compose_lock = true;
                self.typing_notes = true;
                Task::none()
            }
            Message::NoteTurn(v) => {
                self.note_draft.turn_index = v;
                self.note_compose_lock = true;
                Task::none()
            }
            Message::SaveNote => self.save_note(),
            Message::RequestDelete(nid) => self.request_delete(nid),
            Message::PopOutWindow => self.pop_out_window(),
            Message::Hotkey => self.on_hotkey(),
            Message::ListScroll(win) => {
                self.list_window = win;
                Task::none()
            }
            Message::TimelineScroll { y, height } => {
                if height > 1.0 {
                    self.tl_view_h = height;
                }
                // Iced's scrollable owns the pixel range (cards wrap). Do not
                // clamp to n * TIMELINE_ROW_H or the thumb fights real height.
                self.tl_scroll_y = y.max(0.0);
                if let Some(pos) = self.timeline_focus_pos() {
                    if index_outside_visible(
                        self.tl_scroll_y,
                        self.tl_view_h,
                        TIMELINE_ROW_H,
                        self.tl_filter.len(),
                        TIMELINE_OVERSCAN,
                        pos,
                    ) {
                        self.timeline_focus = None;
                    }
                }
                if should_load_previous_timeline(
                    self.tl_scroll_y,
                    self.timeline_offset,
                    self.timeline_loading,
                ) {
                    return self.load_previous_timeline();
                }
                let n = self.filtered_timeline().len();
                let shown = (self.tl_scroll_y / TIMELINE_ROW_H) as usize + 8;
                if n > 0 && shown + 4 >= n && !self.timeline_complete() {
                    return self.load_more_timeline();
                }
                Task::none()
            }
            Message::TurnScroll { y, height } => {
                if height > 1.0 {
                    self.turn_view_h = height;
                }
                let n = self
                    .overview
                    .as_ref()
                    .map(|o| o.turns.turns.len())
                    .unwrap_or(0);
                self.turn_scroll_y = clamp_scroll(y, n as f32 * TURN_ROW_H, self.turn_view_h);
                Task::none()
            }
            Message::FindingExpand { id, open } => {
                if open {
                    self.findings_open.insert(id);
                } else {
                    self.findings_open.remove(&id);
                }
                Task::none()
            }
            Message::NoteExpand { id, open } => {
                if open {
                    self.notes_open.insert(id);
                } else {
                    self.notes_open.remove(&id);
                }
                Task::none()
            }
            Message::FollowDraft(s) => {
                self.follow_draft = s;
                Task::none()
            }
            Message::SendFollow => self.send_follow(),
            Message::MarkDone => self.mark_done(),
            Message::CopyPath => self.copy_path(),
            Message::CopyText(s) => self.copy_text(s),
            Message::ExtractAction { key, action } => {
                if !matches!(action, iced::widget::text_editor::Action::Edit(_)) {
                    if let Some(buf) = self.extracts.get_mut(&key) {
                        buf.perform(action);
                    }
                }
                Task::none()
            }
            Message::ToastDismiss(id) => {
                self.toasts.dismiss(id);
                Task::none()
            }
            Message::FollowDone(result) => {
                match result {
                    Ok(_) => {
                        self.follow_draft.clear();
                        self.toasts.push_success("Follow-up sent");
                    }
                    Err(e) => {
                        self.toasts.push_danger(e);
                    }
                }
                Task::none()
            }
            Message::Tick => self.on_tick(),
            Message::FocusSearch(attempt) => self.on_focus_search(attempt),
            Message::RawEvent(ev) => self.on_event(ev),
            Message::Inited(Ok(_)) => {
                self.mark_up();
                self.status = format!(
                    "ready · {}",
                    control::default_socket_path()
                        .file_name()
                        .and_then(|s| s.to_str())
                        .unwrap_or("control.sock")
                );
                Task::none()
            }
            Message::Inited(Err(e)) => {
                self.mark_down(&e);
                Task::none()
            }
            Message::ListLoaded { quiet, result } => match result {
                Ok(v) => {
                    if quiet {
                        if let Ok(page) = decode_session_list_response(&v) {
                            if !page.unchanged
                                && !page.delta
                                && page.matched > page.sessions.len() as i64
                            {
                                return fetch_list(quiet, 0);
                            }
                        }
                        self.apply_list(v, quiet);
                        return Task::none();
                    }
                    self.apply_list(v.clone(), quiet);
                    let more = self.continue_catalog_pages(&v);
                    if self.sessions.is_empty() {
                        more
                    } else if self.overview.is_none() {
                        Task::batch([more, self.load_overview(false)])
                    } else {
                        more
                    }
                }
                Err(e) => {
                    self.mark_down(&e);
                    Task::none()
                }
            },
            Message::ListPage { offset: _, result } => match result {
                Ok(v) => {
                    let before = self.all_sessions.len();
                    self.apply_list(v.clone(), true);
                    if self.all_sessions.len() <= before {
                        Task::none()
                    } else {
                        self.continue_catalog_pages(&v)
                    }
                }
                Err(e) => {
                    self.mark_down(&e);
                    Task::none()
                }
            },
            Message::OverviewLoaded {
                gen,
                sid,
                quiet,
                result,
            } => {
                if gen != self.overview_gen {
                    return Task::none();
                }
                match result {
                    Ok(data) => {
                        let ov = match decode_overview(&data) {
                            Ok(o) => o,
                            Err(e) => {
                                self.mark_down(&e);
                                return Task::none();
                            }
                        };
                        patch_list_row_from_meta(&mut self.all_sessions, &sid, &ov.meta);
                        patch_list_row_from_meta(&mut self.sessions, &sid, &ov.meta);
                        self.emit_session_notices();
                        if !quiet {
                            self.status = format!("{sid} · {}", ov.meta.status);
                        }
                        self.overview = Some(ov);
                        self.overview_sid = sid.clone();
                        self.overview_pending.clear();
                        self.rebuild_marks();
                        self.rebuild_tl_filter();
                        self.bind_turn_extracts();
                        self.mark_up();
                        if should_fetch_timeline(self.tab == Tab::Timeline) {
                            return Task::batch([
                                self.ensure_timeline(sid, false),
                                if quiet {
                                    Task::none()
                                } else {
                                    self.focus_overlay()
                                },
                            ]);
                        }
                        if !quiet {
                            return self.focus_overlay();
                        }
                    }
                    Err(e) => {
                        if !quiet {
                            self.overview = None;
                            self.overview_sid.clear();
                            self.overview_pending.clear();
                        }
                        self.mark_down(&e);
                    }
                }
                Task::none()
            }
            Message::TimelineLoaded {
                gen,
                sid,
                offset,
                append,
                advance,
                result,
            } => {
                if self.timeline_search_pending || gen != self.timeline_gen {
                    return Task::none();
                }
                self.timeline_loading = false;
                match result {
                    Ok(data) => {
                        let page = match decode_timeline_page(&data) {
                            Ok(p) => p,
                            Err(e) => {
                                self.mark_down(&e);
                                return Task::none();
                            }
                        };
                        let batch = page.events;
                        let total = if page.total > 0 {
                            page.total
                        } else {
                            self.timeline_total
                        };
                        let old_offset = self.timeline_offset;
                        let added =
                            if append && self.timeline_sid == sid && !self.timeline.is_empty() {
                                let merged = merge_timeline_by_index(&self.timeline, &batch);
                                let n = merged.added;
                                self.timeline = merged.events;
                                n
                            } else {
                                self.timeline = batch.clone();
                                batch.len()
                            };
                        self.timeline_sid = sid.clone();
                        self.timeline_total = total;
                        let page_off = if page.limit > 0 || !batch.is_empty() {
                            page.offset
                        } else {
                            offset
                        };
                        self.timeline_offset =
                            timeline_window_start(self.timeline_offset, page_off, !append, advance);
                        self.timeline_next = timeline_page_next(
                            page_off,
                            batch.len() as u32,
                            self.timeline_next,
                            advance,
                        );
                        if self.timeline_total > 0 {
                            self.timeline_next = self.timeline_next.min(self.timeline_total);
                        }
                        self.rebuild_tl_filter();
                        self.mark_up();
                        if let Some(ix) = self.timeline_focus {
                            if self.timeline_expanded.contains(&ix) {
                                self.bind_event_extract(ix);
                            }
                        }
                        if self.timeline.len() > TIMELINE_BUFFER_CAP {
                            self.timeline = trim_timeline_buffer(
                                std::mem::take(&mut self.timeline),
                                self.timeline_focus,
                                TIMELINE_BUFFER_CAP,
                            );
                        }
                        let mut tasks = Vec::new();
                        if !append {
                            if self.timeline_focus_pos().is_some() {
                                tasks.push(self.scroll_focus_into_view());
                            }
                            if let Some(ix) = self.timeline_focus {
                                if self.timeline_expanded.contains(&ix) {
                                    tasks.push(self.fetch_open_event(ix));
                                }
                            }
                        }
                        if append && advance && added > 0 && page_off < old_offset {
                            let y = scroll_after_prepend(self.tl_scroll_y, added, TIMELINE_ROW_H);
                            self.tl_scroll_y = y;
                            tasks.push(operation::scroll_to(
                                self.tl_scroll_id.clone(),
                                AbsoluteOffset { x: 0.0, y },
                            ));
                        }
                        if should_load_previous_timeline(
                            self.tl_scroll_y,
                            self.timeline_offset,
                            false,
                        ) {
                            if let Some(next_sid) = self.selected_sid() {
                                tasks.push(self.fill_timeline_before(next_sid));
                            }
                        }
                        if !tasks.is_empty() {
                            return Task::batch(tasks);
                        }
                    }
                    Err(e) => self.mark_down(&e),
                }
                Task::none()
            }
            Message::NoteSaved(result) => {
                self.note_saving = false;
                match result {
                    Ok(snap) => {
                        self.apply_notes_snapshot(&snap);
                        self.note_draft = NoteDraft::default();
                        self.note_compose_lock = false;
                        self.typing_notes = false;
                        self.toasts.push_success("Note saved");
                        self.mark_up();
                        self.status = "Note saved".into();
                    }
                    Err(e) => {
                        if !is_soft_notes_save_error(&e) {
                            self.mark_down(&e);
                        } else {
                            crate::log::error(&format!("note save (soft): {e}"));
                        }
                        self.status = format!("Note save failed: {e}");
                        self.status_err = true;
                    }
                }
                Task::none()
            }
            Message::NoteDeleted { id, result } => {
                match result {
                    Ok(snap) => {
                        self.apply_notes_snapshot(&snap);
                        if self.note_draft.id == id {
                            self.note_draft = NoteDraft::default();
                            self.note_compose_lock = false;
                        }
                        self.mark_up();
                        self.status = "Note deleted".into();
                    }
                    Err(e) => {
                        if !is_soft_notes_save_error(&e) {
                            self.mark_down(&e);
                        } else {
                            crate::log::error(&format!("note delete (soft): {e}"));
                        }
                        self.status = format!("Note delete failed: {e}");
                        self.status_err = true;
                    }
                }
                Task::none()
            }
            Message::WindowId(id) => {
                let Some(id) = id else {
                    return Task::none();
                };
                self.window_id = Some(id);
                let mut tasks = vec![delayed_focus(0), self.apply_native_chrome(id)];
                if !self.window_mode {
                    tasks.push(self.place_overlay(id));
                }
                Task::batch(tasks)
            }
            Message::WindowPos(pos) => {
                if let Some(p) = pos {
                    if p.x.abs() > 8.0 || p.y.abs() > 8.0 {
                        self.palette_origin = Some(p);
                    }
                }
                Task::none()
            }
            Message::X11Focus { xid, attempt } => self.after_x11_focus(xid, attempt),
            Message::Hide => self.hide_palette(),
            Message::Tray(action) => self.on_tray(action),
            Message::MdLink(url) => {
                self.status = url;
                self.status_err = false;
                Task::none()
            }
        }
    }

    fn view(&self, _window: window::Id) -> Element<'_, Message> {
        view::layout(self)
    }
}

impl Hud {
    pub fn query(&self) -> &str {
        &self.query
    }
    pub fn sessions(&self) -> &[SessionRow] {
        if self.query.trim().is_empty() {
            &self.all_sessions
        } else {
            &self.sessions
        }
    }
    pub fn active(&self) -> usize {
        self.active
    }
    pub fn tab(&self) -> Tab {
        self.tab
    }
    pub fn overview(&self) -> Option<&Overview> {
        self.overview.as_ref()
    }
    pub fn overview_sid(&self) -> &str {
        &self.overview_sid
    }
    pub fn overview_pending(&self) -> &str {
        &self.overview_pending
    }
    pub fn timeline_query(&self) -> &str {
        &self.timeline_query
    }
    pub fn timeline_query_draft(&self) -> &str {
        &self.timeline_query_draft
    }
    pub fn timeline_kind(&self) -> KindFilter {
        self.timeline_kind
    }
    pub fn timeline_focus(&self) -> Option<i64> {
        self.timeline_focus
    }
    pub fn timeline_expanded(&self) -> &HashSet<i64> {
        &self.timeline_expanded
    }
    pub fn is_timeline_expanded(&self, index: i64) -> bool {
        self.timeline_expanded.contains(&index)
    }
    pub fn extract(&self, key: ExtractKey) -> Option<&iced::widget::text_editor::Content> {
        self.extracts.get(&key)
    }
    pub fn extract_src(&self, key: ExtractKey) -> Option<&str> {
        self.extract_src.get(&key).map(String::as_str)
    }

    fn bind_extract_text(&mut self, key: ExtractKey, src: &str) {
        if self.extract_src.get(&key).map(String::as_str) == Some(src) {
            return;
        }
        self.extract_src.insert(key, src.to_string());
        self.extracts
            .insert(key, iced::widget::text_editor::Content::with_text(src));
    }

    fn bind_event_extract(&mut self, index: i64) {
        let src = self
            .timeline
            .iter()
            .find(|e| e.index == index)
            .map(event_body_text)
            .unwrap_or_default();
        if src.is_empty() {
            return;
        }
        self.bind_extract_text(ExtractKey::Event(index), &src);
    }

    fn bind_turn_extracts(&mut self) {
        let Some(o) = &self.overview else {
            return;
        };
        let rows: Vec<(ExtractKey, String)> = o
            .turns
            .turns
            .iter()
            .flat_map(|t| {
                [
                    (ExtractKey::TurnUser(t.turn_index), t.summary.clone()),
                    (
                        ExtractKey::TurnAsst(t.turn_index),
                        t.assistant_summary.clone(),
                    ),
                ]
            })
            .collect();
        let m = &o.meta;
        let git = match (m.git_repo.is_empty(), m.git_branch.is_empty()) {
            (true, true) => "—".into(),
            (false, true) => m.git_repo.clone(),
            (true, false) => m.git_branch.clone(),
            (false, false) => format!("{} · {}", m.git_repo, m.git_branch),
        };
        let findings = if o.findings.total > 0 {
            o.findings.total.to_string()
        } else {
            o.findings.count.to_string()
        };
        let pairs = [
            ("session", m.session_id.clone()),
            ("events", m.num_events.to_string()),
            (
                "tools",
                format!("{} ({} errors)", m.tool_call_count, m.error_count),
            ),
            ("turns", o.turns.total.to_string()),
            ("findings", findings),
            ("notes", o.notes.count.to_string()),
            ("git", git),
            ("path", m.path.clone()),
            ("title", m.title.clone()),
            ("summary", o.summary.clone()),
        ];
        for (key, src) in rows {
            if !src.is_empty() {
                self.bind_extract_text(key, &src);
            }
        }
        for (id, src) in pairs {
            if !src.is_empty() {
                self.bind_extract_text(ExtractKey::Overview(id), &src);
            }
        }
    }

    fn copy_text(&mut self, text: String) -> Task<Message> {
        let text = text.trim().to_string();
        if text.is_empty() {
            self.toasts.push_warning("Nothing to copy");
            return Task::none();
        }
        self.toasts.push_success("Copied");
        icedtea::host::copy_text(text)
    }

    fn yank_active(&mut self) -> Task<Message> {
        if self.tab == Tab::Timeline {
            if let Some(ix) = self.timeline_focus {
                if let Some(ev) = self.timeline.iter().find(|e| e.index == ix) {
                    return self.copy_text(extract_event(ev));
                }
            }
        }
        Task::none()
    }

    pub fn timeline_events(&self) -> &[crate::wire::TimelineEvent] {
        &self.timeline
    }
    pub fn timeline_loading(&self) -> bool {
        self.timeline_loading
    }
    pub fn note_draft(&self) -> &NoteDraft {
        &self.note_draft
    }
    pub fn note_saving(&self) -> bool {
        self.note_saving
    }
    pub fn note_delete_armed(&self) -> &str {
        &self.note_delete_armed
    }
    pub fn status(&self) -> &str {
        &self.status
    }
    pub fn status_err(&self) -> bool {
        self.status_err
    }
    pub fn hotkey_hint(&self) -> &str {
        &self.hotkey_hint
    }
    pub fn window_mode(&self) -> bool {
        self.window_mode
    }
    pub fn theme_name(&self) -> &str {
        &self.theme_name
    }
    pub fn tokens(&self) -> crate::theme::Tokens {
        crate::theme::tokens(&self.theme_name)
    }
    pub fn tea_tokens(&self) -> icedtea::theme::Tokens {
        crate::theme::tea_tokens(&self.theme_name)
    }
    pub fn search_id(&self) -> Id {
        self.search_id.clone()
    }
    pub fn tl_search_id(&self) -> Id {
        self.tl_search_id.clone()
    }
    pub fn notes_schema(&self) -> Vec<SchemaField> {
        notes_schema_fields(self.overview.as_ref())
    }
    pub fn filtered_indices(&self) -> &[usize] {
        &self.tl_filter
    }
    pub fn filtered_timeline(&self) -> Vec<&TimelineEvent> {
        self.tl_filter
            .iter()
            .filter_map(|&i| self.timeline.get(i))
            .collect()
    }
    pub fn timeline_meta(&self) -> String {
        if self.overview_sid.is_empty() {
            return String::new();
        }
        timeline_range_label(
            self.timeline_offset,
            self.filtered_timeline().len(),
            self.timeline_total,
        )
    }
    pub fn card_marks(
        &self,
    ) -> (
        &std::collections::HashMap<i64, CardMark>,
        &std::collections::HashMap<i64, CardMark>,
    ) {
        (&self.turn_marks, &self.event_marks)
    }

    pub fn timeline_complete(&self) -> bool {
        !self.timeline_sid.is_empty()
            && timeline_coverage_complete(self.timeline.len(), self.timeline_total)
    }

    fn selected_sid(&self) -> Option<String> {
        self.sessions()
            .get(self.active)
            .map(|r| r.session_id.clone())
            .filter(|s| !s.is_empty())
    }

    fn selected_rpc_ref(&self) -> Option<String> {
        let row = self.sessions().get(self.active)?;
        let r = session_rpc_ref(&row.path, &row.session_id);
        if r.is_empty() {
            None
        } else {
            Some(r)
        }
    }

    fn overview_rpc_ref(&self) -> String {
        if let Some(o) = &self.overview {
            let r = session_rpc_ref(&o.meta.path, &self.overview_sid);
            if !r.is_empty() {
                return r;
            }
        }
        self.selected_rpc_ref()
            .or_else(|| self.selected_sid())
            .unwrap_or_default()
    }

    pub fn list_window(&self) -> icedtea::collection::VisibleWindow {
        self.list_window
    }

    pub fn list_scroll_id(&self) -> Id {
        self.list_scroll_id.clone()
    }

    pub fn tl_scroll_y(&self) -> f32 {
        self.tl_scroll_y
    }

    pub fn tl_view_h(&self) -> f32 {
        self.tl_view_h
    }

    pub fn turn_scroll_y(&self) -> f32 {
        self.turn_scroll_y
    }

    pub fn timeline_scroll_id(&self) -> Id {
        self.tl_scroll_id.clone()
    }

    pub fn timeline_focus_pos(&self) -> Option<usize> {
        let focus = self.timeline_focus?;
        self.tl_filter
            .iter()
            .position(|&i| self.timeline.get(i).is_some_and(|ev| ev.index == focus))
    }

    pub fn session_tile_height(&self, index: usize) -> f32 {
        let title = self
            .sessions()
            .get(index)
            .map(SessionRow::display_title)
            .unwrap_or("");
        let meta = self
            .session_metas
            .get(index)
            .map(String::as_str)
            .unwrap_or("");
        let has_ctx = self
            .sessions()
            .get(index)
            .map(|r| context_fraction(r.context_window_usage_pct, &r.context_usage_compact) > 0.0)
            .unwrap_or(false);
        session_card_height(title, meta, has_ctx)
    }

    fn session_list_height(&self) -> f32 {
        session_list_content_height(self.sessions().iter().enumerate().map(|(i, row)| {
            (
                row.display_title(),
                self.session_metas.get(i).map(String::as_str).unwrap_or(""),
                context_fraction(row.context_window_usage_pct, &row.context_usage_compact) > 0.0,
            )
        }))
    }

    fn ensure_active_visible(&mut self) -> Task<Message> {
        let view_h = self.list_window.viewport.max(80.0);
        let mut top = 8.0;
        for i in 0..self.active {
            top += self.session_tile_height(i);
        }
        let bot = top + self.session_tile_height(self.active);
        let mut y = self.list_window.scroll;
        if top < y {
            y = top;
        } else if bot > y + view_h {
            y = (bot - view_h).max(0.0);
        }
        y = clamp_scroll(y, self.session_list_height(), view_h);
        self.list_window.scroll = y;
        operation::scroll_to(self.list_scroll_id.clone(), AbsoluteOffset { x: 0.0, y })
    }

    fn selected_status(&self) -> String {
        if let Some(o) = &self.overview {
            let s = o.meta.status_label();
            if !s.is_empty() {
                return s;
            }
        }
        self.sessions()
            .get(self.active)
            .map(|r| r.status.clone())
            .unwrap_or_default()
    }

    fn mark_up(&mut self) {
        self.status_err = false;
    }

    fn mark_down(&mut self, err: &str) {
        crate::log::error(err);
        self.status_err = true;
        self.status = control_down_message(err);
        self.toasts.push_danger(self.status.clone());
    }

    fn reset_detail_chrome(&mut self) {
        self.tab = Tab::Overview;
        self.timeline_query.clear();
        self.timeline_query_draft.clear();
        self.timeline_search_pending = false;
        self.timeline_kind = KindFilter::All;
        self.timeline.clear();
        self.timeline_sid.clear();
        self.timeline_total = 0;
        self.timeline_offset = 0;
        self.timeline_next = 0;
        self.tl_scroll_y = 0.0;
        self.turn_scroll_y = 0.0;
        self.timeline_gen += 1;
        self.timeline_focus = None;
        self.timeline_expanded.clear();
        self.turns_open.clear();
        self.findings_open.clear();
        self.notes_open.clear();
        self.extracts.clear();
        self.extract_src.clear();
        self.note_draft = NoteDraft::default();
        self.note_compose_lock = false;
        self.typing_notes = false;
        self.overview = None;
        self.overview_sid.clear();
        self.tl_filter.clear();
        self.turn_marks.clear();
        self.event_marks.clear();
    }

    fn rebuild_tl_filter(&mut self) {
        if self.timeline_sid != self.overview_sid {
            self.tl_filter.clear();
            return;
        }
        self.tl_filter =
            filter_timeline_indices(&self.timeline, self.timeline_kind, &self.timeline_query);
    }

    fn rebuild_marks(&mut self) {
        match &self.overview {
            Some(o) => {
                let (turns, events) = card_marks_from_overview(o);
                self.turn_marks = turns;
                self.event_marks = events;
            }
            None => {
                self.turn_marks.clear();
                self.event_marks.clear();
            }
        }
    }

    fn apply_list(&mut self, listed: Value, quiet: bool) {
        let page = decode_session_list_response(&listed).ok();
        if let Some(ref page) = page {
            if page.revision > 0 {
                self.catalog_revision = page.revision;
            }
            if quiet && page.unchanged {
                self.mark_up();
                return;
            }
            if page.delta {
                let incoming = page.sessions.clone();
                self.all_sessions =
                    patch_catalog_delta(&self.all_sessions, incoming, &page.removed);
                self.rerank_visible();
                self.emit_session_notices();
                self.mark_up();
                if !quiet {
                    self.status = format!("{} sessions · ready", self.all_sessions.len());
                }
                return;
            }
        }
        let incoming = page
            .as_ref()
            .map(|p| p.sessions.clone())
            .unwrap_or_else(|| decode_session_list(&listed).unwrap_or_default());
        let matched = page.as_ref().map(|p| p.matched).unwrap_or(0);
        let incomplete = page.as_ref().is_some_and(|p| p.incomplete || p.building);
        self.catalog_busy = incomplete;
        let delta = page.as_ref().is_some_and(|p| p.delta);
        if !self.all_sessions.is_empty()
            && is_partial_list_page(
                incoming.len(),
                matched,
                delta,
                page.as_ref().is_some_and(|p| p.incomplete),
                page.as_ref().is_some_and(|p| p.building),
            )
        {
            if incoming.is_empty() {
                return;
            }
            self.all_sessions = patch_catalog_delta(&self.all_sessions, incoming, &[]);
            self.rerank_visible();
            self.emit_session_notices();
            self.mark_up();
            return;
        }
        let rows = merge_catalog_rows(&self.all_sessions, incoming);
        if (quiet || incomplete) && rows.is_empty() && !self.all_sessions.is_empty() {
            return;
        }
        self.all_sessions = rows;
        self.rerank_visible();
        self.emit_session_notices();
        self.mark_up();
        if !quiet {
            if self.sessions().is_empty() {
                self.status = if self.query.trim().is_empty() {
                    self.status_err = true;
                    crate::log::error("no sessions from control");
                    "No sessions from control · is groket serve running?".into()
                } else {
                    format!("No matches for “{}”", self.query.trim())
                };
            } else {
                self.status = format!("{} sessions · ready", self.all_sessions.len());
            }
        }
    }

    fn emit_session_notices(&mut self) {
        let seed = self.seen_status.is_empty();
        let rows: Vec<(String, String, String)> = self
            .all_sessions
            .iter()
            .map(|r| {
                (
                    r.session_id.clone(),
                    r.display_title().to_string(),
                    list_status_label(&r.status, &r.outcome),
                )
            })
            .collect();
        for notice in crate::desktop::notices_from_rows(&mut self.seen_status, &rows, seed) {
            crate::desktop::post(notice);
        }
    }

    fn continue_catalog_pages(&self, listed: &Value) -> Task<Message> {
        let Ok(page) = decode_session_list_response(listed) else {
            return Task::none();
        };
        if page.delta || page.unchanged {
            return Task::none();
        }
        match next_list_offset(
            self.all_sessions.len(),
            first_list_fetch().0,
            page.matched,
            page.incomplete || page.building,
        ) {
            Some(offset) => fetch_list_page(offset),
            None => Task::none(),
        }
    }

    fn rerank_visible(&mut self) {
        let keep = self
            .sessions()
            .get(self.active)
            .map(|r| r.session_id.clone())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| self.overview_sid.clone());
        if self.query.trim().is_empty() {
            self.sessions.clear();
        } else {
            let idxs =
                fuzzy_filter_indices(self.query.trim(), &self.all_sessions, SessionRow::haystack);
            let mut ranked: Vec<SessionRow> = idxs
                .into_iter()
                .filter_map(|i| self.all_sessions.get(i).cloned())
                .collect();
            ranked.sort_by(|a, b| {
                b.sort_epoch
                    .partial_cmp(&a.sort_epoch)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| a.session_id.cmp(&b.session_id))
            });
            self.sessions = ranked;
        }
        self.session_metas = self.sessions().iter().map(session_row_meta).collect();
        let n = self.sessions().len();
        let keep_at = if keep.is_empty() {
            None
        } else {
            self.sessions().iter().position(|r| r.session_id == keep)
        };
        if let Some(idx) = keep_at {
            self.active = idx;
        } else if !keep.is_empty() {
            self.active = 0;
        } else if self.active >= n {
            self.active = n.saturating_sub(1);
        }
        let view_h = self.list_window.viewport.max(1.0);
        self.list_window.scroll =
            clamp_scroll(self.list_window.scroll, self.session_list_height(), view_h);
    }

    fn load_overview(&mut self, quiet: bool) -> Task<Message> {
        let Some(sid) = self.selected_sid() else {
            self.overview = None;
            self.overview_sid.clear();
            self.overview_pending.clear();
            return Task::none();
        };
        let Some(rpc_ref) = self.selected_rpc_ref() else {
            return Task::none();
        };
        self.overview_pending = sid.clone();
        self.overview_gen += 1;
        let gen = self.overview_gen;
        Task::perform(
            rpc(move || control::session_overview(&rpc_ref)),
            move |result| Message::OverviewLoaded {
                gen,
                sid: sid.clone(),
                quiet,
                result,
            },
        )
    }

    fn scroll_focus_into_view(&mut self) -> Task<Message> {
        let Some(pos) = self.timeline_focus_pos() else {
            return Task::none();
        };
        let y = pos as f32 * TIMELINE_ROW_H;
        self.tl_scroll_y = y;
        operation::scroll_to(self.tl_scroll_id.clone(), AbsoluteOffset { x: 0.0, y })
    }

    fn jump_timeline(&mut self, index: i64) -> Task<Message> {
        self.timeline_focus = Some(index);
        self.timeline_expanded.clear();
        self.timeline_expanded.insert(index);
        self.tab = Tab::Timeline;
        self.timeline_query.clear();
        self.timeline_query_draft.clear();
        self.timeline_search_pending = false;
        self.timeline_kind = KindFilter::All;
        self.rebuild_tl_filter();
        if self.timeline.iter().any(|e| e.index == index) {
            self.bind_event_extract(index);
            return Task::batch([self.scroll_focus_into_view(), self.fetch_open_event(index)]);
        }
        if let Some(sid) = self.selected_sid() {
            return self.ensure_timeline(sid, true);
        }
        Task::none()
    }

    fn ensure_timeline(&mut self, sid: String, force: bool) -> Task<Message> {
        if !force && self.timeline_sid == sid && !self.timeline.is_empty() && !self.timeline_loading
        {
            return Task::none();
        }
        self.timeline_gen += 1;
        let gen = self.timeline_gen;
        self.timeline_loading = true;
        if force || self.timeline_sid != sid {
            self.timeline.clear();
            self.timeline_total = 0;
            self.timeline_offset = 0;
            self.timeline_next = 0;
            self.tl_scroll_y = 0.0;
            self.tl_filter.clear();
        }
        fetch_timeline(TimelineFetch {
            rpc_ref: self.overview_rpc_ref(),
            sid,
            offset: 0,
            append: false,
            advance: true,
            gen,
            limit: 40,
            kind: self.timeline_kind.wire_name().to_string(),
            query: self.timeline_query.clone(),
            around: if self.timeline_query.trim().is_empty()
                && self.timeline_kind == KindFilter::All
            {
                self.timeline_focus
            } else {
                None
            },
            at_index: None,
            content_chars: TIMELINE_PREVIEW_CHARS,
        })
    }

    fn fill_timeline_before(&mut self, sid: String) -> Task<Message> {
        if self.timeline_search_pending || self.timeline_loading {
            return Task::none();
        }
        let Some((off, limit)) = previous_timeline_page(self.timeline_offset, TIMELINE_CHUNK)
        else {
            return Task::none();
        };
        let gen = self.timeline_gen;
        self.timeline_loading = true;
        fetch_timeline(TimelineFetch {
            rpc_ref: self.overview_rpc_ref(),
            sid,
            offset: off,
            append: true,
            advance: true,
            gen,
            limit,
            kind: self.timeline_kind.wire_name().to_string(),
            query: self.timeline_query.clone(),
            around: None,
            at_index: None,
            content_chars: TIMELINE_PREVIEW_CHARS,
        })
    }

    fn load_previous_timeline(&mut self) -> Task<Message> {
        if self.tab != Tab::Timeline {
            return Task::none();
        }
        let Some(sid) = self.selected_sid() else {
            return Task::none();
        };
        if self.timeline_sid != sid {
            return Task::none();
        }
        self.fill_timeline_before(sid)
    }

    fn fill_timeline(&mut self, sid: String) -> Task<Message> {
        if self.timeline_search_pending || self.timeline_complete() || self.timeline_loading {
            return Task::none();
        }
        let off = if self.timeline.is_empty() {
            0
        } else {
            self.timeline_next
        };
        let gen = self.timeline_gen;
        self.timeline_loading = true;
        fetch_timeline(TimelineFetch {
            rpc_ref: self.overview_rpc_ref(),
            sid,
            offset: off,
            append: true,
            advance: true,
            gen,
            limit: TIMELINE_CHUNK,
            kind: self.timeline_kind.wire_name().to_string(),
            query: self.timeline_query.clone(),
            around: None,
            at_index: None,
            content_chars: TIMELINE_PREVIEW_CHARS,
        })
    }

    fn fetch_open_event(&mut self, index: i64) -> Task<Message> {
        if self.overview_sid.is_empty() || self.timeline_search_pending {
            return Task::none();
        }
        let gen = self.timeline_gen;
        self.timeline_loading = true;
        fetch_timeline(self.open_event_fetch(index, gen))
    }

    fn open_event_fetch(&self, index: i64, gen: u64) -> TimelineFetch {
        TimelineFetch {
            rpc_ref: self.overview_rpc_ref(),
            sid: self.overview_sid.clone(),
            offset: 0,
            append: true,
            advance: false,
            gen,
            limit: 1,
            kind: self.timeline_kind.wire_name().to_string(),
            query: self.timeline_query.clone(),
            around: None,
            at_index: Some(index),
            content_chars: TIMELINE_OPEN_CHARS,
        }
    }

    fn load_more_timeline(&mut self) -> Task<Message> {
        if self.tab != Tab::Timeline || self.timeline_loading || self.timeline_complete() {
            return Task::none();
        }
        let Some(sid) = self.selected_sid() else {
            return Task::none();
        };
        if self.timeline_sid != sid {
            return Task::none();
        }
        self.fill_timeline(sid)
    }

    fn refresh_timeline_tail(&mut self, sid: String) -> Task<Message> {
        if self.timeline_search_pending || self.timeline_loading {
            return Task::none();
        }
        if self.timeline_sid.is_empty() {
            return self.ensure_timeline(sid, false);
        }
        if self.timeline_sid != sid {
            return Task::none();
        }
        let gen = self.timeline_gen;
        fetch_timeline(TimelineFetch {
            rpc_ref: self.overview_rpc_ref(),
            sid,
            offset: self.timeline_next.saturating_sub(4),
            append: true,
            advance: true,
            gen,
            limit: LIVE_TAIL_LIMIT,
            kind: self.timeline_kind.wire_name().to_string(),
            query: self.timeline_query.clone(),
            around: None,
            at_index: None,
            content_chars: TIMELINE_PREVIEW_CHARS,
        })
    }

    fn open_note(&mut self, nid: &str) {
        let Some(o) = &self.overview else {
            self.tab = Tab::Notes;
            return;
        };
        let Some(n) = o.notes.notes.iter().find(|r| r.id == nid) else {
            self.tab = Tab::Notes;
            return;
        };
        let mut fields = Vec::new();
        if let Some(map) = n.fields.as_object() {
            for (k, v) in map {
                fields.push((
                    k.clone(),
                    match v {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    },
                ));
            }
        }
        self.note_draft = NoteDraft {
            id: nid.to_string(),
            turn_index: n.turn_index.map(|i| i.to_string()).unwrap_or_default(),
            event_index: n
                .event_indices
                .first()
                .map(|x| x.to_string())
                .unwrap_or_default(),
            fields,
        };
        self.note_compose_lock = true;
        self.notes_open.insert(nid.to_string());
        self.tab = Tab::Notes;
    }

    fn save_note(&mut self) -> Task<Message> {
        let sid = self.overview_rpc_ref();
        let Some(o) = &self.overview else {
            self.status = "Select a session before saving a note".into();
            return Task::none();
        };
        if sid.is_empty() {
            self.status = "Select a session before saving a note".into();
            return Task::none();
        }
        if !self.note_draft.has_content() {
            self.status = "Enter a note field before saving".into();
            return Task::none();
        }
        let rev = o.notes.revision.clone();
        let mut id = self.note_draft.id.trim().to_string();
        if id.is_empty() {
            id = new_note_id();
        }
        let mut turn_index = 0i64;
        let turn_raw = self.note_draft.turn_index.trim();
        if !turn_raw.is_empty() {
            match turn_raw.parse::<i64>() {
                Ok(n) if n >= 0 => turn_index = n,
                _ => {
                    self.status = "Turn must be a non-negative integer".into();
                    return Task::none();
                }
            }
        }
        let prev = o.notes.notes.iter().find(|n| n.id == id);
        let mut fields = json!({});
        if let Some(p) = prev {
            if let Some(obj) = p.fields.as_object() {
                fields = Value::Object(obj.clone());
            }
        }
        if let Some(map) = fields.as_object_mut() {
            for (k, v) in &self.note_draft.fields {
                map.insert(k.clone(), json!(v));
            }
        }
        let mut event_indices = prev
            .map(|p| json!(p.event_indices.clone()))
            .unwrap_or_else(|| json!([]));
        if prev.is_none() && !self.note_draft.event_index.trim().is_empty() {
            if let Ok(n) = self.note_draft.event_index.trim().parse::<i64>() {
                event_indices = json!([n]);
            }
        }
        let note = json!({
            "id": id,
            "turnIndex": turn_index,
            "fields": fields,
            "eventIndices": event_indices,
        });
        self.note_saving = true;
        Task::perform(
            rpc(move || control::notes_upsert(&sid, note, &rev)),
            Message::NoteSaved,
        )
    }

    fn request_delete(&mut self, nid: String) -> Task<Message> {
        if nid.is_empty() {
            return Task::none();
        }
        if self.note_delete_armed == nid {
            self.note_delete_armed.clear();
            self.note_delete_until = None;
            return self.delete_note(nid);
        }
        self.note_delete_armed = nid;
        self.note_delete_until = Some(Instant::now() + Duration::from_millis(2500));
        self.status = "Press Delete again to confirm".into();
        Task::none()
    }

    fn delete_note(&mut self, nid: String) -> Task<Message> {
        let sid = self.overview_rpc_ref();
        let Some(o) = &self.overview else {
            return Task::none();
        };
        let rev = o.notes.revision.clone();
        Task::perform(
            rpc({
                let id = nid.clone();
                move || control::notes_delete(&sid, &id, &rev)
            }),
            move |result| Message::NoteDeleted {
                id: nid.clone(),
                result,
            },
        )
    }

    fn apply_notes_snapshot(&mut self, snap: &Value) {
        {
            let Some(o) = self.overview.as_mut() else {
                return;
            };
            if let Some(block) = NotesBlock::from_control_snapshot(snap, &o.notes) {
                o.notes = block;
            }
        }
        self.rebuild_marks();
    }

    fn win_task(&self, f: impl FnOnce(window::Id) -> Task<Message>) -> Task<Message> {
        match self.window_id {
            Some(id) => f(id),
            None => Task::none(),
        }
    }

    fn place_overlay(&self, id: window::Id) -> Task<Message> {
        if let Some(origin) = place::active_palette_origin(HUD_W, HUD_H) {
            window::move_to(id, origin)
        } else if let Some(origin) = self.palette_origin {
            window::move_to(id, origin)
        } else {
            window::position(id).map(Message::WindowPos)
        }
    }

    fn apply_native_chrome(&self, id: window::Id) -> Task<Message> {
        let overlay = !self.window_mode;
        window::run(id, move |handle| {
            if !crate::macoswin::apply(handle, overlay) {
                eprintln!("groket-hud: native chrome apply missed the window");
            }
        })
        .discard()
    }

    fn pop_out_window(&mut self) -> Task<Message> {
        if self.window_mode {
            return Task::none();
        }
        self.window_mode = true;
        self.visible = true;
        self.palette_live = true;
        crate::macoswin::set_desktop_app(true);
        #[cfg(target_os = "linux")]
        crate::x11focus::release_keyboard();
        let old = self.window_id.take();
        let (id, open) = window::open(app_window_settings());
        self.window_id = Some(id);
        let close_old = match old {
            Some(prev) if prev != id => window::close(prev),
            _ => Task::none(),
        };
        Task::batch([open.map(|id| Message::WindowId(Some(id))), close_old])
    }

    fn dismiss_window(&mut self) -> Task<Message> {
        self.window_mode = false;
        self.visible = false;
        self.palette_live = false;
        #[cfg(target_os = "linux")]
        crate::x11focus::release_keyboard();
        let close = match self.window_id.take() {
            Some(id) => window::close(id),
            None => Task::none(),
        };
        crate::macoswin::set_desktop_app(false);
        close
    }

    fn hide_palette(&mut self) -> Task<Message> {
        if self.window_mode {
            return Task::none();
        }
        self.visible = false;
        self.palette_live = false;
        #[cfg(target_os = "linux")]
        crate::x11focus::release_keyboard();
        match self.window_id {
            Some(id) => window::set_mode(id, Mode::Hidden),
            None => Task::none(),
        }
    }

    fn show_palette(&mut self) -> Task<Message> {
        if overlay_already_mapped(self.visible, self.window_mode, self.window_id.is_some()) {
            return self.focus_overlay();
        }
        self.window_mode = false;
        self.visible = true;
        self.palette_live = true;
        self.last_live = Instant::now();
        self.sync_theme();
        if self.window_id.is_none() {
            let (id, open) = window::open(palette_window_settings());
            self.window_id = Some(id);
            return Task::batch([
                open.map(|id| Message::WindowId(Some(id))),
                delayed_focus(0),
                fetch_list(true, self.catalog_revision),
            ]);
        }
        let chrome = match self.window_id {
            Some(id) => Task::batch([
                window::set_mode(id, Mode::Windowed),
                window::set_level(id, window::Level::AlwaysOnTop),
                window::resize(id, Size::new(HUD_W, HUD_H)),
                self.apply_native_chrome(id),
                self.place_overlay(id),
            ]),
            None => Task::none(),
        };
        Task::batch([
            chrome,
            delayed_focus(0),
            fetch_list(true, self.catalog_revision),
        ])
    }

    fn sync_theme(&mut self) {
        let name = prefs::theme_name();
        if name != self.theme_name {
            self.theme_name = name;
        }
    }

    fn x11_focus_only(&self, attempt: u8) -> Task<Message> {
        let Some(id) = self.window_id else {
            return Task::none();
        };
        #[cfg(target_os = "linux")]
        {
            if !crate::x11focus::x11_grab_needed() {
                let _ = attempt;
                return window::gain_focus(id);
            }
            Task::batch([
                window::gain_focus(id),
                window::raw_id::<Message>(id).map(move |xid| Message::X11Focus { xid, attempt }),
            ])
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = attempt;
            window::gain_focus(id)
        }
    }

    fn focus_overlay(&self) -> Task<Message> {
        self.on_focus_search(0)
    }

    fn on_focus_search(&self, attempt: u8) -> Task<Message> {
        if !self.visible {
            return Task::none();
        }
        let input = if self.note_compose_lock {
            Task::none()
        } else {
            operation::focus(self.search_id.clone())
        };
        Task::batch([self.x11_focus_only(attempt), input])
    }

    fn after_x11_focus(&self, xid: u64, attempt: u8) -> Task<Message> {
        #[cfg(target_os = "linux")]
        {
            if !crate::x11focus::focus_window(xid) && attempt < 8 {
                return delayed_focus(attempt.saturating_add(1));
            }
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (xid, attempt);
        }
        Task::none()
    }

    fn on_hotkey(&mut self) -> Task<Message> {
        if self.visible && !self.window_mode {
            self.hide_palette()
        } else if self.visible && self.window_mode {
            self.win_task(window::gain_focus)
        } else {
            self.show_palette()
        }
    }

    fn on_tray(&mut self, action: crate::tray::TrayAction) -> Task<Message> {
        match action {
            crate::tray::TrayAction::Show => self.show_palette(),
            crate::tray::TrayAction::Quit => self.quit(),
        }
    }

    fn quit(&mut self) -> Task<Message> {
        self.visible = false;
        self.palette_live = false;
        #[cfg(target_os = "linux")]
        crate::x11focus::release_keyboard();
        let close = match self.window_id.take() {
            Some(id) => window::close(id),
            None => Task::none(),
        };
        Task::batch([close, iced::exit()])
    }

    fn on_tick(&mut self) -> Task<Message> {
        let now = Instant::now();
        let dt = now.saturating_duration_since(self.last_tick).as_millis() as u64;
        self.last_tick = now;
        self.toasts.tick(dt.max(1));
        self.spin_phase = (self.spin_phase + 0.05) % 1.0;
        self.sync_theme();
        if let Some(until) = self.note_delete_until {
            if Instant::now() >= until {
                self.note_delete_armed.clear();
                self.note_delete_until = None;
                self.status = "Delete cancelled".into();
            }
        }
        let mut cmds = Vec::new();
        let notifies: Vec<(String, Value)> = if let Ok(mut g) = self.notify_q.lock() {
            g.drain(..).collect()
        } else {
            vec![]
        };
        let notify_pairs: Vec<(String, String)> = notifies
            .iter()
            .map(|(method, params)| {
                let sid = params
                    .get("sessionId")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                (method.clone(), sid)
            })
            .collect();
        for (method, params) in &notifies {
            if method != "analysis/changed" {
                continue;
            }
            let sid = params
                .get("sessionId")
                .and_then(Value::as_str)
                .unwrap_or("");
            let title = self
                .all_sessions
                .iter()
                .find(|r| r.session_id == sid)
                .map(|r| r.display_title().to_string())
                .unwrap_or_default();
            if let Some(n) =
                crate::desktop::take_analysis_notice(&mut self.seen_analysis, params, &title)
            {
                crate::desktop::post(n);
            }
        }
        let selected = self.selected_sid().unwrap_or_default();
        let live = session_needs_live_poll(
            &self.selected_status(),
            self.overview.as_ref().map(|o| &o.turns),
        );
        let any_live = live
            || self
                .all_sessions
                .iter()
                .any(|r| session_needs_live_poll(&r.status, None));
        let elapsed = self.last_live.elapsed().as_millis() as u64;
        let plan = plan_tick(TickInput {
            notifies: &notify_pairs,
            selected_sid: &selected,
            overview_sid: &self.overview_sid,
            palette_live: self.palette_live && self.visible,
            list_elapsed_ms: elapsed,
            selected_live: live,
            any_live,
            on_timeline: self.tab == Tab::Timeline,
            notes_locked: self.note_compose_lock,
        });
        if plan.fetch_list {
            cmds.push(fetch_list(true, self.catalog_revision));
        }
        if plan.load_overview {
            cmds.push(self.load_overview(true));
        }
        if plan.refresh_timeline {
            if let Some(sid) = self.selected_sid() {
                cmds.push(self.refresh_timeline_tail(sid));
            }
        }
        if plan.fetch_list || plan.load_overview || plan.refresh_timeline {
            self.last_live = Instant::now();
        }
        Task::batch(cmds)
    }

    pub fn toasts(&self) -> &icedtea::toast::ToastQueue {
        &self.toasts
    }
    pub fn catalog_busy(&self) -> bool {
        self.catalog_busy
    }
    pub fn spin_phase(&self) -> f32 {
        self.spin_phase
    }
    pub fn finding_expanded(&self, id: &str) -> bool {
        self.findings_open.contains(id)
    }
    pub fn note_expanded(&self, id: &str) -> bool {
        self.notes_open.contains(id)
    }
    pub fn turn_expanded(&self, turn: i64) -> bool {
        self.turns_open.contains(&turn)
    }
    pub fn follow_draft(&self) -> &str {
        &self.follow_draft
    }
    pub fn selected_awaiting(&self) -> bool {
        crate::live::is_live_status(&self.selected_status())
            && self
                .selected_status()
                .to_ascii_lowercase()
                .contains("await")
    }

    fn send_follow(&mut self) -> Task<Message> {
        let prompt = self.follow_draft.trim().to_string();
        if prompt.is_empty() {
            self.toasts.push_warning("Follow-up is empty");
            return Task::none();
        }
        let Some(sid) = self.selected_rpc_ref() else {
            self.toasts.push_warning("No session");
            return Task::none();
        };
        Task::perform(
            rpc(move || control::session_follow_up(&sid, &prompt, false)),
            Message::FollowDone,
        )
    }

    fn mark_done(&mut self) -> Task<Message> {
        let Some(sid) = self.selected_rpc_ref() else {
            self.toasts.push_warning("No session");
            return Task::none();
        };
        Task::perform(
            rpc(move || control::session_done(&sid)),
            Message::FollowDone,
        )
    }

    fn copy_path(&mut self) -> Task<Message> {
        let path = self
            .sessions
            .get(self.active)
            .map(|r| r.path.clone())
            .filter(|p| !p.is_empty())
            .or_else(|| self.overview.as_ref().map(|o| o.meta.path.clone()))
            .unwrap_or_default();
        if path.is_empty() {
            self.toasts.push_warning("No path");
            return Task::none();
        }
        self.toasts.push_success("Copied path");
        icedtea::host::copy_text(path)
    }

    fn on_event(&mut self, ev: Event) -> Task<Message> {
        match ev {
            Event::Window(window::Event::CloseRequested) => {
                if self.window_mode {
                    return self.dismiss_window();
                }
                return self.hide_palette();
            }
            Event::Keyboard(keyboard::Event::KeyPressed { key, modifiers, .. }) => {
                return self.on_key(key, modifiers);
            }
            Event::Mouse(iced::mouse::Event::ButtonPressed(_)) if self.visible => {
                return self.on_focus_search(0);
            }
            _ => {}
        }
        Task::none()
    }

    fn on_key(&mut self, key: Key, modifiers: KeyMods) -> Task<Message> {
        if matches!(key, Key::Named(Named::Escape)) {
            return self.hide_palette();
        }
        if modifiers.command() || modifiers.control() {
            if let Key::Character(c) = &key {
                if let Some(n) = c.chars().next().and_then(|ch| ch.to_digit(10)) {
                    if (1..=5).contains(&n) {
                        return self.update(Message::SetTab(Tab::ALL[(n as usize) - 1]));
                    }
                }
            }
        }
        if self.typing_notes {
            return Task::none();
        }
        if matches!(key, Key::Character(ref c) if c.as_str() == "/") && self.tab == Tab::Timeline {
            return operation::focus(self.tl_search_id.clone());
        }
        if matches!(key, Key::Character(ref c) if c.eq_ignore_ascii_case("y"))
            || ((modifiers.command() || modifiers.control())
                && modifiers.shift()
                && matches!(key, Key::Character(ref c) if c.eq_ignore_ascii_case("c")))
        {
            return self.yank_active();
        }
        if matches!(key, Key::Named(Named::Tab)) && (modifiers.control() || modifiers.command()) {
            let i = Tab::ALL.iter().position(|t| *t == self.tab).unwrap_or(0);
            let next = if modifiers.shift() {
                (i + Tab::ALL.len() - 1) % Tab::ALL.len()
            } else {
                (i + 1) % Tab::ALL.len()
            };
            return self.update(Message::SetTab(Tab::ALL[next]));
        }
        match key {
            Key::Named(Named::ArrowDown) if !self.sessions().is_empty() => {
                self.active = (self.active + 1) % self.sessions().len();
                self.reset_detail_chrome();
                Task::batch([self.ensure_active_visible(), self.load_overview(false)])
            }
            Key::Named(Named::ArrowUp) if !self.sessions().is_empty() => {
                let n = self.sessions().len();
                self.active = (self.active + n - 1) % n;
                self.reset_detail_chrome();
                Task::batch([self.ensure_active_visible(), self.load_overview(false)])
            }
            Key::Named(Named::Home) if !self.sessions().is_empty() => {
                self.active = 0;
                self.reset_detail_chrome();
                Task::batch([self.ensure_active_visible(), self.load_overview(false)])
            }
            Key::Named(Named::End) if !self.sessions().is_empty() => {
                self.active = self.sessions().len() - 1;
                self.reset_detail_chrome();
                Task::batch([self.ensure_active_visible(), self.load_overview(false)])
            }
            Key::Named(Named::Enter) => self.load_overview(false),
            _ => Task::none(),
        }
    }
}

impl icedtea::collection::ListModel for Hud {
    fn len(&self) -> usize {
        self.sessions().len()
    }

    fn id(&self, index: usize) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut h = std::collections::hash_map::DefaultHasher::new();
        self.sessions()
            .get(index)
            .map(|r| r.session_id.as_str())
            .unwrap_or("")
            .hash(&mut h);
        h.finish()
    }

    fn title(&self, index: usize) -> &str {
        self.sessions()
            .get(index)
            .map(SessionRow::display_title)
            .unwrap_or("")
    }

    fn meta(&self, index: usize) -> Option<&str> {
        self.session_metas
            .get(index)
            .map(String::as_str)
            .filter(|s| !s.is_empty())
    }
}

fn fetch_list(quiet: bool, since: i64) -> Task<Message> {
    Task::perform(
        rpc(move || {
            if quiet && since > 0 {
                control::session_list("", 10_000, 0, since)
            } else if quiet {
                control::session_list_all("")
            } else {
                let (limit, offset, since_rev) = first_list_fetch();
                control::session_list("", limit, offset, since_rev)
            }
        }),
        move |result| Message::ListLoaded { quiet, result },
    )
}

fn fetch_list_page(offset: u32) -> Task<Message> {
    let limit = first_list_fetch().0;
    Task::perform(
        rpc(move || control::session_list("", limit, offset, 0)),
        move |result| Message::ListPage { offset, result },
    )
}

struct TimelineFetch {
    rpc_ref: String,
    sid: String,
    offset: u32,
    append: bool,
    advance: bool,
    gen: u64,
    limit: u32,
    kind: String,
    query: String,
    around: Option<i64>,
    at_index: Option<i64>,
    content_chars: u32,
}

fn fetch_timeline(req: TimelineFetch) -> Task<Message> {
    Task::perform(
        rpc(move || {
            control::session_timeline(control::TimelineRequest {
                session: &req.rpc_ref,
                offset: req.offset,
                limit: req.limit,
                content_chars: req.content_chars,
                kind: &req.kind,
                query: &req.query,
                around_index: req.around,
                at_index: req.at_index,
            })
        }),
        move |result| Message::TimelineLoaded {
            gen: req.gen,
            sid: req.sid.clone(),
            offset: req.offset,
            append: req.append,
            advance: req.advance,
            result,
        },
    )
}

fn interesting_hud_event(event: Event, status: event::Status, _id: window::Id) -> Option<Message> {
    match event {
        Event::Window(window::Event::CloseRequested) => Some(Message::RawEvent(event)),
        // iced 0.14 text_input unfocuses on Escape and marks the event Captured.
        // Overlay hide must still fire while search or notes hold focus.
        Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Named(Named::Escape),
            ..
        }) if icedtea::window::should_hide(
            icedtea::window::HidePolicy::Escape,
            icedtea::window::HideEvent::Escape,
            true,
        ) =>
        {
            Some(Message::Hide)
        }
        Event::Keyboard(keyboard::Event::KeyPressed { .. }) if status == event::Status::Ignored => {
            Some(Message::RawEvent(event))
        }
        _ => None,
    }
}

fn notify_subscription() -> Subscription<Message> {
    Subscription::run(notify_stream)
}

fn notify_stream() -> impl iced::futures::Stream<Item = Message> {
    iced::stream::channel(8, |mut output| async move {
        let (tx, rx) = std::sync::mpsc::sync_channel::<()>(64);
        control::set_notify_wake(tx);
        let rx = std::sync::Arc::new(std::sync::Mutex::new(rx));
        loop {
            let rx = rx.clone();
            let got = tokio::task::spawn_blocking(move || {
                rx.lock().ok().and_then(|guard| guard.recv().ok())
            })
            .await
            .ok()
            .flatten();
            if got.is_none() {
                break;
            }
            if iced::futures::SinkExt::send(&mut output, Message::Tick)
                .await
                .is_err()
            {
                break;
            }
        }
    })
}

async fn rpc<F>(f: F) -> Result<Value, String>
where
    F: FnOnce() -> Result<Value, ControlError> + Send + 'static,
{
    tokio::task::spawn_blocking(f)
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}

fn delayed_focus(attempt: u8) -> Task<Message> {
    let wait_ms = if attempt == 0 {
        30
    } else {
        40 + u64::from(attempt) * 20
    };
    Task::perform(
        async move {
            tokio::time::sleep(Duration::from_millis(wait_ms)).await;
        },
        move |_| Message::FocusSearch(attempt),
    )
}

fn hotkey_subscription() -> Subscription<Message> {
    Subscription::run(hotkey_stream)
}

fn tray_subscription() -> Subscription<Message> {
    Subscription::run(tray_stream)
}

fn tray_stream() -> impl iced::futures::Stream<Item = Message> {
    iced::stream::channel(8, |mut output| async move {
        loop {
            let action = tokio::task::spawn_blocking(crate::tray::recv_action)
                .await
                .ok()
                .and_then(Result::ok);
            let Some(action) = action else {
                break;
            };
            if iced::futures::SinkExt::send(&mut output, Message::Tray(action))
                .await
                .is_err()
            {
                break;
            }
        }
    })
}

fn hotkey_stream() -> impl iced::futures::Stream<Item = Message> {
    iced::stream::channel(8, |mut output| async move {
        loop {
            let pressed = tokio::task::spawn_blocking(|| {
                GlobalHotKeyEvent::receiver()
                    .recv()
                    .map(|ev| ev.state == HotKeyState::Pressed)
                    .unwrap_or(false)
            })
            .await
            .unwrap_or(false);
            if pressed {
                let _ = iced::futures::SinkExt::send(&mut output, Message::Hotkey).await;
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timeline_filter_cache_avoids_per_frame_scan() {
        let mut hud = Hud {
            overview_sid: "s".into(),
            timeline_sid: "s".into(),
            timeline: vec![
                TimelineEvent {
                    index: 0,
                    kind: "user".into(),
                    content: "hello".into(),
                    ..TimelineEvent::default()
                },
                TimelineEvent {
                    index: 1,
                    kind: "tool".into(),
                    content: "run".into(),
                    ..TimelineEvent::default()
                },
                TimelineEvent {
                    index: 2,
                    kind: "agent".into(),
                    content: "ok".into(),
                    ..TimelineEvent::default()
                },
            ],
            ..Hud::default()
        };
        hud.rebuild_tl_filter();
        assert_eq!(hud.filtered_indices(), &[0, 1, 2]);
        hud.timeline_kind = KindFilter::Tools;
        hud.rebuild_tl_filter();
        assert_eq!(hud.filtered_indices(), &[1]);
        assert_eq!(hud.filtered_timeline().len(), 1);
    }

    #[test]
    fn empty_search_uses_all_sessions_without_a_second_copy() {
        let mut hud = Hud {
            all_sessions: vec![SessionRow {
                session_id: "a".into(),
                title: "Alpha".into(),
                ..SessionRow::default()
            }],
            query: String::new(),
            ..Hud::default()
        };
        hud.rerank_visible();
        assert_eq!(hud.sessions().len(), 1);
        assert!(hud.sessions.is_empty());
        assert_eq!(hud.sessions()[0].session_id, "a");
        use icedtea::collection::ListModel;
        assert_eq!(hud.len(), 1);
        assert_eq!(hud.title(0), "Alpha");
    }

    #[test]
    fn turn_scroll_does_not_move_timeline() {
        let mut hud = Hud {
            tl_scroll_y: 400.0,
            ..Hud::default()
        };
        let _ = hud.update(Message::TurnScroll {
            y: 80.0,
            height: 400.0,
        });
        assert!((hud.tl_scroll_y() - 400.0).abs() < f32::EPSILON);
        assert!((hud.turn_scroll_y() - 0.0).abs() < f32::EPSILON);
    }

    #[test]
    fn palette_settings_are_fixed_overlay() {
        let w = palette_window_settings();
        assert_eq!(w.size, Size::new(HUD_W, HUD_H));
        assert!(!w.decorations);
        assert!(!w.resizable);
        assert_eq!(w.level, window::Level::AlwaysOnTop);
        assert!(w.icon.is_some());
        #[cfg(target_os = "linux")]
        assert!(w.platform_specific.override_redirect);
    }

    #[test]
    fn interesting_hud_event_ignores_mouse_motion() {
        let ev = Event::Mouse(iced::mouse::Event::CursorMoved {
            position: Point::new(1.0, 1.0),
        });
        assert!(interesting_hud_event(ev, event::Status::Ignored, window::Id::unique()).is_none());
        let key = Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Named(Named::ArrowDown),
            modified_key: Key::Named(Named::ArrowDown),
            physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::ArrowDown),
            location: iced::keyboard::Location::Standard,
            modifiers: KeyMods::default(),
            text: None,
            repeat: false,
        });
        assert!(
            interesting_hud_event(key.clone(), event::Status::Ignored, window::Id::unique())
                .is_some()
        );
        assert!(
            interesting_hud_event(key, event::Status::Captured, window::Id::unique()).is_none()
        );
    }

    fn escape_pressed() -> Event {
        Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Named(Named::Escape),
            modified_key: Key::Named(Named::Escape),
            physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::Escape),
            location: iced::keyboard::Location::Standard,
            modifiers: KeyMods::default(),
            text: None,
            repeat: false,
        })
    }

    #[test]
    fn captured_escape_still_hides_the_overlay() {
        let id = window::Id::unique();
        let esc = escape_pressed();
        assert!(matches!(
            interesting_hud_event(esc.clone(), event::Status::Captured, id),
            Some(Message::Hide)
        ));
        assert!(matches!(
            interesting_hud_event(esc, event::Status::Ignored, id),
            Some(Message::Hide)
        ));
        let mut hud = Hud {
            visible: true,
            palette_live: true,
            typing_notes: true,
            window_id: Some(window::Id::unique()),
            ..Hud::default()
        };
        let _ = hud.update(Message::Hide);
        assert!(!hud.visible);
        assert!(!hud.palette_live);
    }

    #[test]
    fn app_window_settings_are_decorated() {
        let w = app_window_settings();
        assert!(w.decorations);
        assert!(w.resizable);
        assert_eq!(w.level, window::Level::Normal);
        assert!(w.icon.is_some());
        #[cfg(target_os = "linux")]
        assert!(!w.platform_specific.override_redirect);
    }

    #[test]
    fn selecting_a_session_starts_the_first_timeline_page() {
        let mut hud = Hud {
            all_sessions: vec![SessionRow {
                session_id: "s1".into(),
                path: "/tmp/s1".into(),
                ..SessionRow::default()
            }],
            ..Hud::default()
        };
        let _ = hud.update(Message::SelectSession(0));
        assert_eq!(hud.active(), 0);
        assert!(hud.timeline_loading());
        assert!(hud.timeline_gen > 0);
    }

    #[test]
    fn select_timeline_toggles_expand_on_the_same_index() {
        let mut hud = Hud::default();
        let _ = hud.update(Message::SelectTimeline(7));
        assert!(hud.is_timeline_expanded(7));
        assert_eq!(hud.timeline_focus(), Some(7));
        let _ = hud.update(Message::SelectTimeline(7));
        assert!(!hud.is_timeline_expanded(7));
        assert_eq!(hud.timeline_focus(), None);
        let _ = hud.update(Message::SelectTimeline(7));
        let _ = hud.update(Message::SelectTimeline(9));
        assert!(!hud.is_timeline_expanded(7));
        assert!(hud.is_timeline_expanded(9));
        assert_eq!(hud.timeline_focus(), Some(9));
    }

    #[test]
    fn jump_from_turn_opens_timeline_on_that_event() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                ev_json(0, "a"),
                ev_json(1, "b"),
                ev_json(2, "c"),
                ev_json(3, "user"),
            ],
            10,
            0,
        );
        hud.tab = Tab::Turns;
        let _ = hud.update(Message::JumpTimeline(3));
        assert_eq!(hud.tab(), Tab::Timeline);
        assert!(hud.is_timeline_expanded(3));
        assert_eq!(hud.timeline_focus(), Some(3));
        assert!((hud.tl_scroll_y() - 3.0 * TIMELINE_ROW_H).abs() < f32::EPSILON);
    }

    #[test]
    fn jump_missing_event_reloads_around_it() {
        let mut hud = hud_with_session();
        let gen = hud.timeline_gen;
        hud.tab = Tab::Turns;
        let _ = hud.update(Message::JumpTimeline(99));
        assert_eq!(hud.tab(), Tab::Timeline);
        assert!(hud.is_timeline_expanded(99));
        assert_eq!(hud.timeline_focus(), Some(99));
        assert!(hud.timeline_loading());
        assert!(hud.timeline_gen > gen);
    }

    #[test]
    fn turn_expanders_open_independently() {
        let mut hud = Hud::default();
        assert!(!hud.turn_expanded(2));
        let _ = hud.update(Message::TurnExpand {
            turn: 2,
            open: true,
        });
        assert!(hud.turn_expanded(2));
        let _ = hud.update(Message::TurnExpand {
            turn: 5,
            open: true,
        });
        assert!(hud.turn_expanded(2));
        assert!(hud.turn_expanded(5));
        let _ = hud.update(Message::TurnExpand {
            turn: 2,
            open: false,
        });
        assert!(!hud.turn_expanded(2));
        assert!(hud.turn_expanded(5));
    }

    #[test]
    fn finding_and_note_expanders_open_independently() {
        let mut hud = Hud::default();
        let _ = hud.update(Message::FindingExpand {
            id: "a".into(),
            open: true,
        });
        let _ = hud.update(Message::FindingExpand {
            id: "b".into(),
            open: true,
        });
        assert!(hud.finding_expanded("a"));
        assert!(hud.finding_expanded("b"));
        let _ = hud.update(Message::NoteExpand {
            id: "n1".into(),
            open: true,
        });
        let _ = hud.update(Message::NoteExpand {
            id: "n2".into(),
            open: true,
        });
        assert!(hud.note_expanded("n1"));
        assert!(hud.note_expanded("n2"));
        let _ = hud.update(Message::FindingExpand {
            id: "a".into(),
            open: false,
        });
        assert!(!hud.finding_expanded("a"));
        assert!(hud.finding_expanded("b"));
    }

    #[test]
    fn overview_load_binds_copyable_fields() {
        let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/overview.json");
        let data: Value =
            serde_json::from_str(&std::fs::read_to_string(path).expect("fixture")).expect("json");
        let mut hud = Hud {
            overview_gen: 1,
            ..Hud::default()
        };
        let _ = hud.update(Message::OverviewLoaded {
            gen: 1,
            sid: "sess-wire".into(),
            quiet: true,
            result: Ok(data),
        });
        assert_eq!(
            hud.extract_src(ExtractKey::Overview("session")),
            Some("sess-wire")
        );
        assert_eq!(
            hud.extract_src(ExtractKey::Overview("path")),
            Some("/workspace/sess-wire")
        );
        assert_eq!(hud.extract_src(ExtractKey::Overview("events")), Some("3"));
        assert!(hud.extract(ExtractKey::Overview("session")).is_some());
    }

    #[test]
    fn expanding_an_event_binds_extract_text() {
        use crate::format::event_body_text;
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![ev_json(3, "# hello **md**")],
            10,
            0,
        );
        let _ = hud.update(Message::SelectTimeline(3));
        let ev = hud.timeline.iter().find(|e| e.index == 3).unwrap();
        let src = event_body_text(ev);
        assert!(src.contains("# hello **md**"));
        assert!(!src.contains("#3 "));
        assert!(hud.extract(ExtractKey::Event(3)).is_some());
        assert_eq!(
            hud.extract_src
                .get(&ExtractKey::Event(3))
                .map(String::as_str),
            Some(src.as_str())
        );
    }

    #[test]
    fn timeline_search_does_not_apply_until_debounce() {
        let mut hud = Hud::default();
        let _ = hud.update(Message::TimelineQuery("grep".into()));
        assert_eq!(hud.timeline_query_draft(), "grep");
        assert_eq!(hud.timeline_query(), "");
        assert!(hud.timeline_search_gen > 0);
        let _ = hud.update(Message::TimelineSearchApply(0));
        assert_eq!(hud.timeline_query_draft(), "grep");
        assert_eq!(hud.timeline_query(), "");
    }

    fn ev_json(index: i64, content: &str) -> Value {
        json!({
            "index": index,
            "type": "agent_message_chunk",
            "kind": "agent",
            "content": content,
            "contentLength": content.len(),
            "contentTruncated": content.len() < 80,
        })
    }

    fn hud_with_session() -> Hud {
        Hud {
            tab: Tab::Timeline,
            overview_sid: "s1".into(),
            timeline_sid: "s1".into(),
            timeline_gen: 1,
            all_sessions: vec![SessionRow {
                session_id: "s1".into(),
                path: "/tmp/s1".into(),
                ..SessionRow::default()
            }],
            ..Hud::default()
        }
    }

    fn load_page(
        hud: &mut Hud,
        offset: u32,
        append: bool,
        advance: bool,
        events: Vec<Value>,
        total: u32,
        page_offset: u32,
    ) {
        let gen = hud.timeline_gen;
        let _ = hud.update(Message::TimelineLoaded {
            gen,
            sid: "s1".into(),
            offset,
            append,
            advance,
            result: Ok(json!({
                "sessionId": "s1",
                "total": total,
                "offset": page_offset,
                "limit": events.len(),
                "events": events,
            })),
        });
    }

    #[test]
    fn timeline_query_holds_unfiltered_ids_until_apply() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                ev_json(0, "alpha"),
                ev_json(1, "beta needle"),
                ev_json(2, "gamma"),
            ],
            80,
            0,
        );
        let held: Vec<i64> = hud.timeline.iter().map(|e| e.index).collect();
        assert_eq!(held, vec![0, 1, 2]);
        let gen_before = hud.timeline_gen;
        let _ = hud.update(Message::TimelineQuery("needle".into()));
        assert_eq!(hud.timeline_query_draft(), "needle");
        assert_eq!(hud.timeline_query(), "");
        let after_query: Vec<i64> = hud.timeline.iter().map(|e| e.index).collect();
        assert_eq!(after_query, held);
        let _ = hud.update(Message::LoadMoreTimeline);
        let _ = hud.update(Message::TimelineScroll {
            y: 10_000.0,
            height: 400.0,
        });
        // In-flight fill from the old gen, or a new-query slice on the new gen,
        // must not mix into the held page.
        let _ = hud.update(Message::TimelineLoaded {
            gen: gen_before,
            sid: "s1".into(),
            offset: 3,
            append: true,
            advance: true,
            result: Ok(json!({
                "sessionId": "s1",
                "total": 2,
                "offset": 0,
                "limit": 2,
                "events": [ev_json(1, "beta needle"), ev_json(50, "later needle")],
            })),
        });
        let _ = hud.update(Message::TimelineLoaded {
            gen: hud.timeline_gen,
            sid: "s1".into(),
            offset: 0,
            append: true,
            advance: true,
            result: Ok(json!({
                "sessionId": "s1",
                "total": 2,
                "offset": 0,
                "limit": 2,
                "events": [ev_json(1, "beta needle"), ev_json(50, "later needle")],
            })),
        });
        let shown: Vec<i64> = hud.timeline.iter().map(|e| e.index).collect();
        assert_eq!(shown, held);
        assert!(!shown.contains(&50));
    }

    #[test]
    fn around_page_advances_from_owner_offset() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                ev_json(20, "a"),
                ev_json(21, "b"),
                ev_json(22, "c"),
                ev_json(23, "d"),
            ],
            100,
            12,
        );
        assert_eq!(hud.timeline_next, 16);
        assert_eq!(hud.timeline_offset, 12);
        assert_eq!(hud.timeline_meta(), "13-16 of 100");
        let first: Vec<i64> = hud.timeline.iter().map(|e| e.index).collect();
        assert_eq!(first, vec![20, 21, 22, 23]);
        // A later jump replaces the prefix window; the pager must follow
        // the new owner offset, not keep "1-60 of …".
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![ev_json(2000, "late"), ev_json(2001, "later")],
            7663,
            1192,
        );
        assert_eq!(hud.timeline_offset, 1192);
        assert_eq!(hud.timeline_meta(), "1193-1194 of 7663");
    }

    #[test]
    fn scroll_up_after_jump_loads_earlier_events() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                ev_json(20, "a"),
                ev_json(21, "b"),
                ev_json(22, "c"),
                ev_json(23, "d"),
            ],
            100,
            12,
        );
        assert_eq!(hud.timeline_offset, 12);
        assert!(
            hud.timeline_loading,
            "landing mid-session must fetch the page above"
        );
        let y_before = hud.tl_scroll_y();
        let earlier: Vec<Value> = (8..20).map(|i| ev_json(i, "prev")).collect();
        load_page(&mut hud, 0, true, true, earlier, 100, 0);
        assert_eq!(hud.timeline_offset, 0);
        let ids: Vec<i64> = hud.timeline.iter().map(|e| e.index).collect();
        assert!(ids.contains(&8));
        assert!(ids.contains(&23));
        assert!(hud.tl_scroll_y() > y_before);
        assert_eq!(hud.timeline_meta(), "1-16 of 100");
    }

    #[test]
    fn expand_refetches_open_chars_and_paints_full_json() {
        use crate::format::{body_paint, BodyPaint};
        let mut hud = hud_with_session();
        let stub = "{".to_string() + &"\"k\":".repeat(200) + "1";
        assert!(!stub.ends_with('}'));
        assert_eq!(body_paint("agent", &stub, true), BodyPaint::Plain);
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![json!({
                "index": 3,
                "type": "tool_call_update",
                "kind": "tool_result",
                "content": stub,
                "contentLength": 9000,
                "contentTruncated": true,
            })],
            10,
            0,
        );
        let req = hud.open_event_fetch(3, hud.timeline_gen);
        assert_eq!(req.content_chars, TIMELINE_OPEN_CHARS);
        assert_eq!(req.at_index, Some(3));
        assert!(!req.advance);
        assert!(req.append);
        let next_before = hud.timeline_next;
        let _ = hud.update(Message::SelectTimeline(3));
        assert!(hud.is_timeline_expanded(3));
        let full = stub.clone() + "}";
        let gen = hud.timeline_gen;
        let _ = hud.update(Message::TimelineLoaded {
            gen,
            sid: "s1".into(),
            offset: 0,
            append: true,
            advance: false,
            result: Ok(json!({
                "sessionId": "s1",
                "total": 10,
                "offset": 3,
                "limit": 1,
                "events": [{
                    "index": 3,
                    "type": "tool_call_update",
                    "kind": "tool_result",
                    "content": full,
                    "contentLength": 9000,
                    "contentTruncated": false,
                }],
            })),
        });
        assert_eq!(hud.timeline_next, next_before);
        let ev = hud.timeline.iter().find(|e| e.index == 3).expect("row");
        assert!(ev.content.ends_with('}'));
        assert_eq!(body_paint(&ev.kind, &ev.content, true), BodyPaint::Json);
        assert!(hud.is_timeline_expanded(3));
    }

    #[test]
    fn copy_path_warns_without_a_session() {
        let mut hud = Hud::default();
        let _ = hud.update(Message::CopyPath);
        assert!(hud.toasts().iter().any(|t| t.text.contains("No path")));
    }

    #[test]
    fn boot_is_palette_not_window() {
        let hud = Hud::default();
        assert!(!hud.window_mode());
    }

    #[test]
    fn close_requested_keeps_process_in_window_mode() {
        // Decorated close must not iced::exit — dismiss_window closes the
        // surface and the summon hotkey opens a fresh palette.
        let mut hud = Hud {
            window_mode: true,
            visible: true,
            palette_live: true,
            ..Hud::default()
        };
        let _ = hud.dismiss_window();
        assert!(!hud.window_mode());
        assert!(!hud.visible);
        assert!(hud.window_id.is_none());
    }

    #[test]
    fn overlay_already_mapped_skips_remap() {
        assert!(overlay_already_mapped(true, false, true));
        assert!(!overlay_already_mapped(false, false, true));
        assert!(!overlay_already_mapped(true, true, true));
        assert!(!overlay_already_mapped(true, false, false));
    }

    #[test]
    fn tray_show_on_visible_overlay_does_not_clear_window() {
        let id = window::Id::unique();
        let mut hud = Hud {
            visible: true,
            palette_live: true,
            window_mode: false,
            window_id: Some(id),
            ..Hud::default()
        };
        let _ = hud.on_tray(crate::tray::TrayAction::Show);
        assert!(hud.visible);
        assert!(!hud.window_mode);
        assert_eq!(hud.window_id, Some(id));
    }

    #[test]
    fn tray_quit_clears_the_window_id() {
        let id = window::Id::unique();
        let mut hud = Hud {
            visible: true,
            palette_live: true,
            window_id: Some(id),
            ..Hud::default()
        };
        let _ = hud.on_tray(crate::tray::TrayAction::Quit);
        assert!(hud.window_id.is_none());
        assert!(!hud.visible);
        assert!(!hud.palette_live);
    }

    #[test]
    fn tray_show_reveals_hidden_palette() {
        let mut hud = Hud {
            visible: false,
            palette_live: false,
            window_mode: true,
            ..Hud::default()
        };
        let _ = hud.on_tray(crate::tray::TrayAction::Show);
        assert!(hud.visible);
        assert!(hud.palette_live);
        assert!(!hud.window_mode);
    }

    #[test]
    fn window_id_none_does_not_replace_live_id() {
        let id = window::Id::unique();
        let mut hud = Hud {
            window_id: Some(id),
            ..Hud::default()
        };
        let _ = hud.update(Message::WindowId(None));
        assert_eq!(hud.window_id, Some(id));
    }
}
