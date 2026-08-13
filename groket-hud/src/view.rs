//! Palette layout.

use std::cell::RefCell;
use std::collections::HashMap;
use std::hash::{Hash, Hasher};

use iced::mouse;
use iced::widget::canvas::{self, Canvas};

use iced::widget::{
    column, container, image, markdown, mouse_area, responsive, row, scrollable, stack, text,
    text_input, Space,
};
use iced::{Alignment, Color, Element, Length, Padding, Point, Rectangle, Renderer, Size, Theme};
use icedtea::a11y::{A11y, Role};
use icedtea::toast::ToastKind;
use icedtea::variant::Variant;

use crate::app::{ExtractKey, Hud, Message};
use crate::brand;
use crate::format::{
    body_paint, capped_display, display_tool_output, event_brand_role, fmt_duration,
    format_note_time, human_event_type_label, image_result_path, list_status_label,
    looks_like_markdown, note_fields_view, origin_label, overview_fields, sanitize_console_text,
    status_tone, timeline_body_text, timeline_count_caption, timeline_query_hit,
    tool_fields_from_raw, BodyPaint, ToolField,
};
use crate::live::{
    context_fraction, finding_severity_rank, finding_severity_title, CardMark, SESSION_LIST_W,
    TIMELINE_OVERSCAN, TURNS_OVERSCAN,
};
use crate::model::{KindFilter, Tab};
use crate::typo;
use crate::wire::{FindingRow, NoteRow, TimelineEvent, TurnRow};

fn context_meter(frac: f32, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let filled = (frac.clamp(0.0, 1.0) * 100.0).round() as u16;
    let empty = 100u16.saturating_sub(filled);
    let track = tea.panel;
    let bar = tea.primary;
    let meter = if filled == 0 {
        container(Space::new())
            .width(Length::Fill)
            .height(3)
            .style(move |_| icedtea::style::fill(track, tea.text))
            .into()
    } else if empty == 0 {
        container(Space::new())
            .width(Length::Fill)
            .height(3)
            .style(move |_| icedtea::style::fill(bar, tea.text))
            .into()
    } else {
        row![
            container(Space::new())
                .width(Length::FillPortion(filled))
                .height(3)
                .style(move |_| icedtea::style::fill(bar, tea.text)),
            container(Space::new())
                .width(Length::FillPortion(empty))
                .height(3)
                .style(move |_| icedtea::style::fill(track, tea.text)),
        ]
        .into()
    };
    icedtea::a11y::attach(meter, &A11y::new("context", Role::Progress))
}

fn rule(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    icedtea::widget::rule_h(tea, A11y::new("rule", Role::Separator))
}

fn empty_sessions(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    icedtea::pattern::status_page("No sessions", "Is groket serve running?", None, tea)
}

fn loading_session(sid: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    column![
        icedtea::widget::placeholder_skeleton(tea, A11y::new("Loading", Role::Progress)),
        icedtea::pattern::status_page("Loading", sid.to_string(), None, tea),
    ]
    .spacing(12)
    .into()
}

fn select_session(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    icedtea::pattern::status_page("Select a session", "", None, tea)
}

fn awaiting_banner(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    column![
        icedtea::widget::banner(
            "Session is awaiting a follow-up",
            Some(("Done".into(), Message::MarkDone)),
            tea,
            A11y::new("awaiting", Role::Status),
        ),
        container(icedtea::widget::themed_text_input(
            "Follow-up prompt",
            hud.follow_draft(),
            Message::FollowDraft,
            Some(Message::SendFollow),
            tea,
            A11y::new("follow-up", Role::TextBox),
            None,
        ))
        .width(Length::Fill),
        icedtea::widget::themed_button(
            "Send follow-up",
            Some(Message::SendFollow),
            tea,
            Variant::Primary,
            A11y::button("Send follow-up"),
        ),
    ]
    .spacing(8)
    .into()
}

fn status_copy(text: &str, err: bool, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let a11y = A11y::new(text.to_string(), Role::Status);
    if err {
        icedtea::widget::info_bar(ToastKind::Danger, text.to_string(), tea, a11y)
    } else {
        icedtea::widget::meta(text.to_string(), tea, a11y)
    }
}

fn tone_variant(tone: &str) -> Variant {
    match tone {
        "complete" => Variant::Success,
        "running" => Variant::Primary,
        "awaiting" | "ending" => Variant::Warning,
        "cancelled" => Variant::Danger,
        _ => Variant::Quiet,
    }
}

fn tool_image(path: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let a11y = A11y::new(path.to_string(), Role::Image);
    if std::path::Path::new(path).is_file() {
        icedtea::widget::image_slot(
            icedtea::widget::ImageSlot::Ready {
                handle: iced::widget::image::Handle::from_path(path),
                fit: iced::ContentFit::Contain,
            },
            Length::Fill,
            Length::Fixed(240.0),
            tea,
            a11y,
        )
    } else {
        icedtea::widget::image_slot(
            icedtea::widget::ImageSlot::Error(path.to_string()),
            Length::Fill,
            Length::Fixed(80.0),
            tea,
            a11y,
        )
    }
}

fn select_bound<'a>(
    hud: &'a Hud,
    id: String,
    fallback: &str,
    tea: icedtea::theme::Tokens,
    face: icedtea::typo::FontFace,
) -> Element<'a, Message> {
    let Some(buf) = hud.field(&id) else {
        return text(fallback.to_string())
            .size(typo::BODY)
            .font(face.font())
            .into();
    };
    let a11y_id = id.clone();
    icedtea::widget::selectable(
        buf,
        move |action| Message::Select {
            id: id.clone(),
            action,
        },
        tea,
        face,
        A11y::new(a11y_id, Role::TextBox),
    )
}

fn selectable<'a>(
    hud: &'a Hud,
    key: ExtractKey,
    fallback: &str,
    tea: icedtea::theme::Tokens,
    face: icedtea::typo::FontFace,
) -> Element<'a, Message> {
    select_bound(hud, key.id(), fallback, tea, face)
}

fn code_inset<'a>(hud: &'a Hud, id: &str, tea: icedtea::theme::Tokens) -> Element<'a, Message> {
    let Some(buf) = hud.field(id) else {
        return text(String::new()).size(typo::BODY).font(typo::MONO).into();
    };
    let id = id.to_string();
    icedtea::widget::code_block(
        buf,
        move |action| Message::Select {
            id: id.clone(),
            action,
        },
        tea,
        A11y::new("code", Role::TextBox),
    )
}

pub fn layout(hud: &Hud) -> Element<'_, Message> {
    let tok = hud.tokens();
    let tea = hud.tokens();
    let mut search = row![
        image(brand::chrome_handle(crate::theme::canvas_is_dark(tok)))
            .width(brand::chrome_width())
            .height(brand::chrome_height()),
        text_input("Search sessions", hud.query())
            .id(hud.search_id())
            .font(typo::UI)
            .size(typo::BODY)
            .on_input(Message::SearchChanged)
            .on_submit(Message::ActivateSelected)
            .padding([8, 10])
            .style(icedtea::style::search_style(tea))
            .width(Length::Fill),
        text(hud.hotkey_hint())
            .size(typo::META)
            .font(typo::MONO)
            .color(tok.muted),
    ]
    .spacing(12)
    .align_y(Alignment::Center);
    if !hud.window_mode() {
        search = search.push(pop_out_control(tok, tea));
    }
    let search = search.padding(Padding::from([12, 16]));

    let list = session_list(hud);
    let detail = detail_pane(hud);
    let body =
        icedtea::pattern::list_detail(list, detail, icedtea::layout::fixed(SESSION_LIST_W), tea);

    let foot = footer(hud, tea);

    let mut stack = column![search, rule(tea), body, rule(tea), foot];
    for t in hud.toasts().iter() {
        let id = t.id;
        stack = stack.push(icedtea::widget::toast_view(
            t,
            Message::ToastDismiss(id),
            tea,
            A11y::new(t.text.clone(), Role::Status),
        ));
    }
    let shell = container(stack)
        .width(Length::Fill)
        .height(Length::Fill)
        .padding(1)
        .style(move |_| icedtea::style::shell(tea));
    let busy = icedtea::widget::busy_overlay(
        shell.into(),
        hud.catalog_busy(),
        hud.spin_phase(),
        tea,
        A11y::new("Catalog", Role::Progress),
    );
    if let Some(origin) = hud.context_origin() {
        return stack![
            busy,
            icedtea::pattern::context_menu(
                hud.context_actions(),
                origin,
                hud.window_size(),
                Message::ContextDismiss,
                tea,
            ),
        ]
        .into();
    }
    busy
}

fn session_list(hud: &Hud) -> Element<'_, Message> {
    responsive(move |size| session_list_at(hud, size.height.max(1.0))).into()
}

fn session_list_at(hud: &Hud, viewport: f32) -> Element<'_, Message> {
    let tea = hud.tokens();
    if hud.sessions().is_empty() {
        return empty_sessions(tea);
    }
    let mut window = hud.list_window();
    window.viewport = viewport.max(1.0);
    let hud_tok = hud.tokens();
    icedtea::widget::list_view(
        hud,
        hud.list_selection(),
        Message::SelectSession,
        tea,
        window,
        icedtea::collection::RowHeights::PerRow(hud.session_heights()),
        1,
        Message::ListScroll,
        "No sessions",
        move |i| {
            let status = hud
                .sessions()
                .get(i)
                .map(|r| crate::format::list_status_label(&r.status, &r.outcome))
                .unwrap_or_default();
            tone_color(status_tone(&status), hud_tok)
        },
        Some(hud.list_scroll_id()),
        // Context fill lives on Overview only — rail meters were noise at catalog scale.
        icedtea::collection::RowFace::Card {
            meter: None::<fn(usize) -> f32>,
        },
        A11y::new("Sessions", Role::List),
    )
}

fn detail_pane(hud: &Hud) -> Element<'_, Message> {
    let session_ready = hud.overview().is_some() || !hud.overview_pending().is_empty();
    let tabs = container(detail_tab_bar(hud, session_ready)).padding(Padding::from([8, 12]));

    let mut stack = column![tabs].spacing(0).height(Length::Fill);
    if hud.tab() == Tab::Timeline && hud.overview().is_some() {
        stack = stack.push(timeline_filter(hud));
    }
    let body: Element<'_, Message> = if hud.overview().is_none() {
        if !hud.overview_pending().is_empty() {
            loading_session(hud.overview_pending(), hud.tokens())
        } else {
            select_session(hud.tokens())
        }
    } else {
        match hud.tab() {
            Tab::Overview => overview_tab(hud),
            Tab::Turns | Tab::Timeline => column![].into(),
            Tab::Findings => findings_tab(hud),
            Tab::Notes => notes_tab(hud),
        }
    };
    let tea = hud.tokens();
    if hud.tab() == Tab::Timeline && hud.overview().is_some() {
        stack = stack.push(
            container(timeline_tab(hud))
                .padding([16, 20])
                .width(Length::Fill)
                .height(Length::Fill),
        );
    } else if hud.tab() == Tab::Turns && hud.overview().is_some() {
        stack = stack.push(
            container(turns_tab(hud))
                .padding([16, 20])
                .width(Length::Fill)
                .height(Length::Fill),
        );
    } else {
        stack = stack.push(icedtea::widget::themed_scroll(
            container(body).padding([16, 20]).width(Length::Fill).into(),
            tea,
            A11y::new("Detail", Role::Group),
            false,
            None,
            None::<fn(scrollable::Viewport) -> Message>,
        ));
    }
    container(stack)
        .width(Length::Fill)
        .height(Length::Fill)
        .into()
}

/// Pane tabs: secondary panes only pressable once a session overview exists.
fn detail_tab_bar(hud: &Hud, session_ready: bool) -> Element<'_, Message> {
    let tea = hud.tokens();
    let active = Tab::ALL.iter().position(|t| *t == hud.tab()).unwrap_or(0);
    let mut r = row![].spacing(4).align_y(Alignment::Center);
    for (i, tab) in Tab::ALL.iter().enumerate() {
        let title = tab.label().to_string();
        let enabled = session_ready || *tab == Tab::Overview;
        // Gate C: disabled secondary tabs must not look live (muted + no press).
        let show_active = enabled && i == active;
        let label = if enabled {
            text(title.clone()).size(typo::META)
        } else {
            text(title.clone()).size(typo::META).color(tea.muted)
        };
        let mut btn = iced::widget::button(label)
            .padding([6, 10])
            .style(icedtea::style::tab_style(tea, show_active));
        if enabled {
            btn = btn.on_press(Message::SetTab(*tab));
        }
        r = r.push(container(btn).padding([2, 2]));
    }
    r.into()
}

fn timeline_filter(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    // Two rows: picks + optional range; full-width search below so it never
    // shares width with Turn/Type (one-row bar clipped or overlapped the field).
    let mut picks = row![
        icedtea::widget::meta("Turn", tea, A11y::new("Turn", Role::Header)),
        icedtea::widget::themed_pick_list(
            hud.events_turn_options(),
            Some(hud.events_turn_selected()),
            Message::EventsTurnPicked,
            tea,
            A11y::new("Turn", Role::ComboBox),
        ),
        icedtea::widget::meta("Type", tea, A11y::new("Type", Role::Header)),
        icedtea::widget::themed_pick_list(
            &KindFilter::ALL[..],
            Some(hud.timeline_kind()),
            Message::TimelineKind,
            tea,
            A11y::new("Type", Role::ComboBox),
        ),
        Space::new().width(Length::Fill),
    ]
    .spacing(8)
    .align_y(Alignment::Center)
    .width(Length::Fill);
    if let Some(cap) = timeline_count_caption(&hud.timeline_meta()) {
        picks = picks.push(icedtea::widget::meta(
            cap.to_string(),
            tea,
            A11y::new(cap.to_string(), Role::Status),
        ));
    }
    let search = container(icedtea::widget::themed_text_input(
        "Search all events",
        hud.timeline_query_draft(),
        Message::TimelineQuery,
        None,
        tea,
        A11y::new("Search all events", Role::TextBox),
        Some(hud.tl_search_id()),
    ))
    .width(Length::Fill);
    column![picks, search]
        .spacing(10)
        .width(Length::Fill)
        .padding(Padding::from([8, 12]))
        .into()
}

/// Fixed label gutter for Overview meta — every value starts on the same x.
const KV_LABEL_W: f32 = 96.0;

fn overview_tab(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    let meta = &o.meta;
    let mut title = meta.title.clone();
    if title.is_empty() {
        title = hud.overview_sid().to_string();
    }
    let mut summary = o.summary.clone();
    if summary.is_empty() {
        summary = meta.summary.clone();
    }
    if summary.is_empty() {
        summary = "No summary text for this session.".into();
    }
    let status = meta.status_label();
    let tone = status_tone(&status);
    let taken = if meta.duration.is_empty() {
        fmt_duration(meta.duration_seconds)
    } else {
        meta.duration.clone()
    };
    // Context % is on the bar below — keep hero to model · origin · duration.
    let hero = format!(
        "{} · {} · {}",
        meta.model,
        origin_label(&meta.origin),
        taken,
    );
    let tok = hud.tokens();
    let tea = hud.tokens();
    let ctx_frac = context_fraction(meta.context_window_usage_pct, meta.context_compact());
    let mut col = column![
        text(title.clone())
            .size(typo::PAGE)
            .font(typo::UI_BOLD)
            .color(tok.text),
        row![
            icedtea::widget::badge(
                if status.is_empty() {
                    "—".to_string()
                } else {
                    status
                },
                tea,
                tone_variant(tone),
                A11y::new("status", Role::Status),
            ),
            icedtea::widget::meta(hero, tea, A11y::new("meta", Role::Status)),
        ]
        .spacing(8)
        .align_y(Alignment::Center),
    ]
    .spacing(8);
    // Progress only where context matters (session detail), and only when known.
    if ctx_frac > 0.0 {
        col = col.push(context_meter(ctx_frac, tea));
    }
    if hud.selected_awaiting() {
        col = col.push(awaiting_banner(hud, tea));
    }
    if o.findings.count > 0 || o.findings.total > 0 {
        let n = if o.findings.total > 0 {
            o.findings.total
        } else {
            o.findings.count
        };
        col = col.push(icedtea::widget::banner(
            format!("{n} findings — open the Findings pane"),
            Some(("Findings".into(), Message::SetTab(Tab::Findings))),
            tea,
            A11y::new("findings", Role::Status),
        ));
    }
    if o.notes.count > 0 {
        col = col.push(icedtea::widget::banner(
            format!("{} notes — open the Notes pane", o.notes.count),
            Some(("Notes".into(), Message::SetTab(Tab::Notes))),
            tea,
            A11y::new("notes", Role::Status),
        ));
    }
    if summary != title && summary != "No summary text for this session." {
        col = col.push(md_body(&summary, 4000, hud.tokens()));
    } else if summary == "No summary text for this session." {
        col = col.push(icedtea::widget::meta(
            summary,
            hud.tokens(),
            A11y::new("summary", Role::Status),
        ));
    }
    for field in overview_fields(meta, &o.turns) {
        col = col.push(kv(hud, field.key, field.label, field.value, field.copyable));
    }
    col.into()
}

thread_local! {
    static MD_ITEMS: RefCell<HashMap<u64, &'static [markdown::Item]>> = RefCell::new(HashMap::new());
}

fn intern_md(src: &str) -> &'static [markdown::Item] {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    src.hash(&mut hasher);
    let key = hasher.finish();
    MD_ITEMS.with(|map| {
        let mut map = map.borrow_mut();
        if let Some(items) = map.get(&key) {
            return *items;
        }
        let leaked: &'static [markdown::Item] =
            Box::leak(icedtea::widget::parse(src).items.into_boxed_slice());
        map.insert(key, leaked);
        leaked
    })
}

fn md_body(src: &str, max_chars: usize, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let cut: String = src.chars().take(max_chars).collect();
    if cut.trim().is_empty() {
        return Space::new().height(0).into();
    }
    if !looks_like_markdown(&cut) {
        return text(cut).size(typo::BODY).font(typo::UI).into();
    }
    icedtea::widget::markdown_view(
        intern_md(&cut),
        tea,
        |url| Message::MdLink(url.to_string()),
        A11y::new("markdown", Role::Group),
    )
}

/// One Overview meta row: shared label gutter + value (selectable when bound).
///
/// Label width is app-owned (`KV_LABEL_W`). icedtea's labeled value row sizes
/// the label to natural text width, so short keys like path would shift values.
fn kv<'a>(
    hud: &'a Hud,
    key: &'static str,
    label: &'static str,
    v: String,
    copy: bool,
) -> Element<'a, Message> {
    let tok = hud.tokens();
    let label_el = text(label)
        .size(typo::META)
        .color(tok.muted)
        .width(Length::Fixed(KV_LABEL_W));
    let value_el: Element<'a, Message> = if copy {
        if let Some(buf) = hud.field(&ExtractKey::Overview(key).id()) {
            let id = ExtractKey::Overview(key).id();
            icedtea::widget::selectable(
                buf,
                move |action| Message::Select {
                    id: id.clone(),
                    action,
                },
                tok,
                icedtea::typo::FontFace::Mono,
                A11y::new(key, Role::TextBox),
            )
        } else {
            text(v)
                .size(typo::BODY)
                .font(typo::MONO)
                .color(tok.text)
                .into()
        }
    } else {
        text(v).size(typo::BODY).color(tok.text).into()
    };
    row![label_el, value_el]
        .spacing(8)
        .align_y(Alignment::Center)
        .width(Length::Fill)
        .into()
}

fn footer(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    let mut table = icedtea::action::ActionTable::new();
    table.insert(icedtea::action::Action::new(
        "keys",
        "Tab fields  ·  Ctrl+1–5 panes  ·  Esc",
        Message::Hide,
    ));
    let hints = table
        .get("keys")
        .map(|a| a.title.clone())
        .unwrap_or_else(|| "Tab fields  ·  Ctrl+1–5 panes  ·  Esc".into());
    let left = status_copy(hud.status(), hud.status_err(), tea);
    let right = icedtea::widget::meta(hints.clone(), tea, A11y::new(hints, Role::Status));
    // Same chrome as pattern::status_bar: status left, ActionTable title right.
    icedtea::a11y::attach(
        container(row![left, Space::new().width(Length::Fill), right,].padding([8, 12]))
            .width(Length::Fill)
            .style(move |_| icedtea::style::footer(tea))
            .into(),
        &A11y::new("statusbar", Role::Status),
    )
}

fn chip_btn(label: String, msg: Message, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    icedtea::widget::chip(
        label.clone(),
        Some(msg),
        None,
        tea,
        Variant::Chip,
        A11y::button(label),
    )
}

fn command_end(child: Element<'static, Message>) -> Element<'static, Message> {
    row![Space::new().width(Length::Fill), child]
        .width(Length::Fill)
        .align_y(Alignment::Center)
        .into()
}

fn card_chips(
    hud: &Hud,
    mark: Option<CardMark>,
    note: Option<Message>,
    jump: Option<Message>,
) -> Element<'static, Message> {
    let tea = hud.tokens();
    let tok = hud.tokens();
    let mut marks = row![].spacing(4);
    if let Some(m) = mark {
        if m.findings > 0 {
            let ev = m.first_finding_event;
            marks = marks.push(chip_btn(
                format!("f{}", m.findings),
                if let Some(ix) = ev {
                    Message::JumpTimeline(ix)
                } else {
                    Message::SetTab(Tab::Findings)
                },
                tea,
            ));
        }
        if m.notes > 0 {
            let nid = m.first_note_id;
            marks = marks.push(chip_btn(
                format!("n{}", m.notes),
                if nid.is_empty() {
                    Message::SetTab(Tab::Notes)
                } else {
                    Message::OpenNote(nid)
                },
                tea,
            ));
        }
        if m.errors > 0 {
            marks = marks.push(
                text(format!("e{}", m.errors))
                    .size(typo::META)
                    .color(tok.danger),
            );
        }
    }
    let mut cmds = row![].spacing(4);
    if let Some(msg) = note {
        cmds = cmds.push(chip_btn("Add note".into(), msg, tea));
    }
    if let Some(msg) = jump {
        cmds = cmds.push(jump_control(msg, tok.muted, tea));
    }
    row![marks, Space::new().width(Length::Fill), cmds]
        .spacing(8)
        .align_y(Alignment::Center)
        .width(Length::Fill)
        .into()
}

fn card_actions(
    actions: Vec<icedtea::action::Action<Message>>,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    icedtea::pattern::command_bar(actions, tea, icedtea::i18n::Direction::Ltr)
}

fn expand_card<'a>(
    title: String,
    child: Element<'a, Message>,
    open: bool,
    on_toggle: impl Fn(bool) -> Message + 'a,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    icedtea::widget::expander(
        title.clone(),
        child,
        icedtea::widget::Peek::Lines(2),
        open,
        on_toggle,
        tea,
        A11y::new(title, Role::Group),
    )
}

/// Closed Turns/Timeline row: flat card (TUI DataTable energy), not expander+Peek.
///
/// Expander always mounts a peeked child tree; at virtual-list density that
/// dominated frame time. Open rows still use [`expand_card`].
fn closed_list_card<'a>(
    title: String,
    face: Element<'a, Message>,
    chips: Element<'a, Message>,
    on_open: Message,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    let header = row![
        text(title)
            .size(typo::BODY)
            .font(typo::UI_BOLD)
            .color(tea.text),
        Space::new().width(Length::Fill),
        text("▸").size(typo::META).color(tea.muted),
    ]
    .spacing(8)
    .align_y(Alignment::Center)
    .width(Length::Fill);
    let body = column![header, face, chips].spacing(6).width(Length::Fill);
    mouse_area(
        container(body)
            .padding(12)
            .width(Length::Fill)
            .style(move |_| icedtea::style::card(tea, false)),
    )
    .on_press(on_open)
    .into()
}

fn turn_title(t: &TurnRow) -> String {
    let label = if t.label.is_empty() {
        format!("turn {}", t.turn_index)
    } else {
        t.label.clone()
    };
    match t.duration_seconds.filter(|s| *s > 0.0).map(fmt_duration) {
        Some(d) => format!("{label}  ·  {d}"),
        None => label,
    }
}

/// Outcome badge + duration / counts for an open turn card (overview-style).
fn turn_stats_row(t: &TurnRow, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let status = if t.open {
        "open".to_string()
    } else {
        list_status_label("", &t.outcome)
    };
    let tone = if t.open {
        "running"
    } else {
        status_tone(&status)
    };
    let taken = t
        .duration_seconds
        .filter(|s| *s > 0.0)
        .map(fmt_duration)
        .unwrap_or_else(|| "—".into());
    let tools = if t.tool_error_count > 0 {
        format!(
            "{} tools · {} tool errors",
            t.tool_call_count, t.tool_error_count
        )
    } else {
        format!("{} tools", t.tool_call_count)
    };
    let prompt = t
        .prompt_index
        .map(|n| n.to_string())
        .unwrap_or_else(|| "—".into());
    let hero = format!(
        "{taken} · {} events · {tools} · prompt {prompt}",
        t.event_count,
    );
    row![
        icedtea::widget::badge(
            status.clone(),
            tea,
            tone_variant(tone),
            A11y::new(status, Role::Status),
        ),
        icedtea::widget::meta(hero.clone(), tea, A11y::new(hero, Role::Status)),
    ]
    .spacing(8)
    .align_y(Alignment::Center)
    .into()
}

fn turn_note(t: &TurnRow) -> Message {
    Message::StartNote {
        turn: t.turn_index.to_string(),
        event: String::new(),
    }
}

fn turn_jump(t: &TurnRow) -> Message {
    t.user_event_index
        .or(t.first_index)
        .map(Message::JumpTimeline)
        .unwrap_or(Message::SetTab(Tab::Timeline))
}

fn event_note(ev: &TimelineEvent) -> Message {
    Message::StartNote {
        turn: ev.turn_index.map(|n| n.to_string()).unwrap_or_default(),
        event: ev.index.to_string(),
    }
}

fn event_type_human(ev: &TimelineEvent) -> String {
    human_event_type_label(&ev.event_type, &ev.type_label, &ev.kind)
}

fn event_title(ev: &TimelineEvent) -> String {
    // Expander title is monochrome; put #index · time here and the colored
    // human type on the face (TUI scan: type column + summary).
    let mut out = format!("#{}", ev.index);
    let time = ev.time.trim();
    if !time.is_empty() {
        out.push_str(" · ");
        out.push_str(time);
    }
    out
}

fn event_face(ev: &TimelineEvent, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let type_color =
        crate::theme::brand_role_color(event_brand_role(&ev.event_type, &ev.kind, ev.is_error));
    // Prefer tool name for tool rows (TUI tool column); else human type label.
    let identity = if !ev.tool_name.trim().is_empty()
        && (ev.kind == "tool" || ev.kind == "tool_result" || ev.event_type.contains("tool"))
    {
        ev.tool_name.trim().to_string()
    } else {
        event_type_human(ev)
    };
    let preview = if ev.preview.is_empty() {
        ev.content.as_str()
    } else {
        ev.preview.as_str()
    };
    let preview = if preview.is_empty() {
        ev.heading.as_str()
    } else {
        preview
    };
    // One scannable line (TUI type + summary columns), not a markdown stack.
    let preview = capped_display(preview, 160);
    if identity.is_empty() && preview.is_empty() {
        return text("—").size(typo::META).color(tea.muted).into();
    }
    if identity.is_empty() {
        return text(preview).size(typo::BODY).color(tea.text).into();
    }
    if preview.is_empty() {
        return text(identity)
            .size(typo::META)
            .font(typo::UI_BOLD)
            .color(type_color)
            .into();
    }
    row![
        text(identity)
            .size(typo::META)
            .font(typo::UI_BOLD)
            .color(type_color),
        text(preview).size(typo::BODY).color(tea.text),
    ]
    .spacing(8)
    .align_y(Alignment::Center)
    .into()
}

fn event_body<'a>(
    hud: &'a Hud,
    ev: &'a TimelineEvent,
    mark: Option<CardMark>,
) -> Element<'a, Message> {
    let tok = hud.tokens();
    let type_color =
        crate::theme::brand_role_color(event_brand_role(&ev.event_type, &ev.kind, ev.is_error));
    let human = event_type_human(ev);
    let mut col = column![].spacing(6);
    if !human.is_empty() {
        col = col.push(
            text(human)
                .size(typo::META)
                .font(typo::UI_BOLD)
                .color(type_color),
        );
    }
    if let Some(hit) = timeline_query_hit(ev, hud.timeline_query()) {
        col = col.push(
            text(format!("matched in {}: {}", hit.field, hit.snippet))
                .size(typo::META)
                .color(tok.muted),
        );
    }
    col = col.push(event_payload(ev, true, hud));
    if ev.content_truncated {
        col = col.push(
            text("Content truncated by control")
                .size(typo::META)
                .color(tok.muted),
        );
    }
    col.push(card_chips(hud, mark, Some(event_note(ev)), None))
        .into()
}

pub(crate) fn finding_jump(f: &FindingRow) -> Message {
    f.primary_event_index
        .or_else(|| f.event_indices.first().copied())
        .map(Message::JumpTimeline)
        .unwrap_or(Message::SetTab(Tab::Overview))
}

fn note_when(n: &NoteRow) -> String {
    if n.updated_at.is_empty() {
        format_note_time(&n.created_at)
    } else {
        format_note_time(&n.updated_at)
    }
}

fn note_body<'a>(
    hud: &'a Hud,
    n: &'a NoteRow,
    body: &str,
    extras: Vec<(String, String)>,
) -> Element<'a, Message> {
    let tea = hud.tokens();
    let turn = n.turn_index.map(|i| i.to_string()).unwrap_or_default();
    let where_when = format!(
        "{} · {}",
        if turn.is_empty() || turn == "null" {
            "Session".into()
        } else {
            format!("Turn {turn}")
        },
        note_when(n),
    );
    let mut card = column![text(where_when).size(typo::META).color(hud.tokens().muted)].spacing(8);
    if !body.is_empty() {
        card = card.push(md_body(body, 4000, tea));
    }
    for (k, v) in extras.into_iter().take(8) {
        card = card.push(
            text(format!("{k}: {v}"))
                .size(typo::META)
                .color(hud.tokens().muted),
        );
    }
    card.push(
        row![
            Space::new().width(Length::Fill),
            card_actions(note_commands(&n.id, hud.note_delete_armed()), tea),
        ]
        .spacing(8)
        .align_y(Alignment::Center),
    )
    .into()
}

fn note_commands(id: &str, delete_armed: &str) -> Vec<icedtea::action::Action<Message>> {
    vec![
        icedtea::action::Action::new("note.edit", "Edit", Message::OpenNote(id.to_string())),
        icedtea::action::Action::new(
            "note.delete",
            if delete_armed == id {
                "Delete?"
            } else {
                "Delete"
            },
            Message::RequestDelete(id.to_string()),
        ),
    ]
}

fn closed_turn_face(summary: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    plain_face(summary, "No user prompt in this turn", 320, tea)
}

/// Closed-card preview only. Markdown parse/layout per visible row was the
/// Turns/Timeline scroll tax; open bodies use selectable / md_body when needed.
fn prompt_face(summary: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    plain_face(summary, "—", 280, tea)
}

fn plain_face(
    summary: &str,
    empty: &'static str,
    max_chars: usize,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    if summary.is_empty() {
        return text(empty).size(typo::BODY).color(tea.muted).into();
    }
    text(capped_display(summary, max_chars))
        .size(typo::BODY)
        .font(typo::UI)
        .color(tea.text)
        .into()
}

/// Fixed Turns card: prompt + light meta + jump/note (no expander / assistant body).
fn turn_list_card(
    hud: &Hud,
    t: &TurnRow,
    mark: Option<CardMark>,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let jump = turn_jump(t);
    let title = text(turn_title(t))
        .size(typo::BODY)
        .font(typo::UI_BOLD)
        .color(tea.text);
    let body = column![
        title,
        turn_stats_row(t, tea),
        closed_turn_face(&t.summary, tea),
        card_chips(hud, mark, Some(turn_note(t)), Some(jump.clone())),
    ]
    .spacing(6)
    .width(Length::Fill);
    mouse_area(
        container(body)
            .padding(12)
            .width(Length::Fill)
            .style(move |_| icedtea::style::card(tea, false)),
    )
    .on_press(jump)
    .into()
}

fn turns_filter(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    container(icedtea::widget::themed_text_input(
        "Search turns",
        hud.turns_query(),
        Message::TurnsQuery,
        None,
        tea,
        A11y::new("Search turns", Role::TextBox),
        Some(hud.turns_search_id()),
    ))
    .width(Length::Fill)
    .padding(Padding {
        top: 0.0,
        right: 0.0,
        bottom: 8.0,
        left: 0.0,
    })
    .into()
}

fn turns_tab(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    let turns: &[TurnRow] = &o.turns.turns;
    let (turn_marks, _) = hud.card_marks();
    let tea = hud.tokens();
    if turns.is_empty() {
        return text("No turns segmented.")
            .size(typo::BODY)
            .color(hud.tokens().muted)
            .into();
    }
    let idxs = hud.filtered_turn_indices();
    let list = if idxs.is_empty() {
        text("No turns match this search.")
            .size(typo::BODY)
            .color(hud.tokens().muted)
            .into()
    } else {
        let heights = hud.turn_heights();
        icedtea::widget::virtual_column(
            heights,
            hud.turn_window(),
            TURNS_OVERSCAN,
            None,
            Message::TurnScroll,
            Some(hud.turn_scroll_id()),
            tea,
            move |i| {
                let Some(&src) = idxs.get(i) else {
                    return Space::new().height(0).into();
                };
                let Some(t) = turns.get(src) else {
                    return Space::new().height(0).into();
                };
                let mark = turn_marks.get(&t.turn_index).cloned();
                column![turn_list_card(hud, t, mark, tea), Space::new().height(4)].into()
            },
            A11y::new("Turns", Role::List),
        )
    };
    column![turns_filter(hud), list]
        .spacing(0)
        .height(Length::Fill)
        .into()
}

fn timeline_tab(hud: &Hud) -> Element<'_, Message> {
    if hud.timeline_query().trim().is_empty()
        && hud.last_timeline().is_none()
        && hud.filtered_indices().is_empty()
        && !hud.timeline_loading()
    {
        // Should be rare: SetTab/All turns loads immediately. Honest fallback.
        return text("Loading events…")
            .size(typo::BODY)
            .color(hud.tokens().muted)
            .into();
    }
    if hud.timeline_loading() && hud.filtered_indices().is_empty() {
        return loading_session("events", hud.tokens());
    }
    let idxs = hud.filtered_indices();
    if idxs.is_empty() {
        if hud.timeline_loading() || !hud.timeline_complete() {
            return text("Loading matching events…")
                .size(typo::BODY)
                .color(hud.tokens().muted)
                .into();
        }
        return text("No events in this filter.")
            .size(typo::BODY)
            .color(hud.tokens().muted)
            .into();
    }
    let (_, ev_marks) = hud.card_marks();
    let tea = hud.tokens();
    let source = hud.timeline_events();
    let cover = hud.timeline_focus_pos();
    let list = icedtea::widget::virtual_column(
        hud.timeline_heights(),
        hud.timeline_window(),
        TIMELINE_OVERSCAN,
        cover,
        Message::TimelineScroll,
        Some(hud.timeline_scroll_id()),
        tea,
        move |i| {
            let Some(&src_i) = idxs.get(i) else {
                return Space::new().height(0).into();
            };
            let Some(ev) = source.get(src_i) else {
                return Space::new().height(0).into();
            };
            let ix = ev.index;
            let open = hud.is_timeline_expanded(ix);
            let mark = ev_marks.get(&ix).cloned();
            let card = if open {
                expand_card(
                    event_title(ev),
                    event_body(hud, ev, mark),
                    true,
                    move |_| Message::SelectTimeline(ix),
                    tea,
                )
            } else {
                closed_list_card(
                    event_title(ev),
                    event_face(ev, tea),
                    card_chips(hud, mark, Some(event_note(ev)), None),
                    Message::SelectTimeline(ix),
                    tea,
                )
            };
            column![card, Space::new().height(2)].into()
        },
        A11y::new("Timeline", Role::List),
    );
    if hud.timeline_complete() {
        return list;
    }
    column![
        list,
        text(if hud.timeline_loading() {
            "Loading more events…"
        } else {
            "More events available — scroll or wait"
        })
        .size(typo::META)
        .color(hud.tokens().muted),
    ]
    .spacing(8)
    .height(Length::Fill)
    .into()
}

fn findings_tab(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    let findings: &[FindingRow] = &o.findings.findings;
    let tea = hud.tokens();
    if findings.is_empty() {
        return icedtea::pattern::status_page(
            "No findings",
            "Run analysis in the TUI so results land in the analysis cache.",
            None,
            tea,
        );
    }
    let mut buckets: [Vec<&FindingRow>; 4] = [vec![], vec![], vec![], vec![]];
    for f in findings {
        let r = finding_severity_rank(&f.severity) as usize;
        buckets[r.min(3)].push(f);
    }
    let mut col = column![icedtea::widget::meta(
        format!("{} findings", findings.len()),
        tea,
        A11y::new("findings-count", Role::Status),
    )]
    .spacing(8);
    for (rank, group) in buckets.iter().enumerate() {
        if group.is_empty() {
            continue;
        }
        col = col.push(icedtea::widget::meta(
            format!("{}  ({})", finding_severity_title(rank as u8), group.len()),
            tea,
            A11y::new(finding_severity_title(rank as u8), Role::Header),
        ));
        for f in group {
            let id = finding_key(f);
            let open = hud.finding_expanded(&id);
            let title = if f.title.is_empty() {
                "Finding".into()
            } else {
                f.title.clone()
            };
            let child = if open {
                finding_body(f, tea)
            } else {
                column![
                    prompt_face(&f.detail, tea),
                    command_end(jump_control(finding_jump(f), hud.tokens().muted, tea)),
                ]
                .spacing(6)
                .into()
            };
            col = col.push(expand_card(
                title,
                child,
                open,
                {
                    let id = id.clone();
                    move |next| Message::FindingExpand {
                        id: id.clone(),
                        open: next,
                    }
                },
                tea,
            ));
        }
    }
    col.into()
}

fn finding_key(f: &FindingRow) -> String {
    if !f.id.is_empty() {
        return f.id.clone();
    }
    format!(
        "{}|{}|{}",
        f.severity,
        f.title,
        f.primary_event_index.unwrap_or(-1)
    )
}

fn finding_body(f: &FindingRow, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let mut card = column![icedtea::widget::badge(
        f.severity.clone(),
        tea,
        tone_variant(status_tone(&f.severity)),
        A11y::new(f.severity.clone(), Role::Status),
    )]
    .spacing(8);
    if !f.detail.is_empty() {
        card = card.push(md_body(&f.detail, 1200, tea));
    }
    card.push(command_end(jump_control(finding_jump(f), tea.muted, tea)))
        .into()
}

fn notes_tab(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    let mut notes: Vec<&NoteRow> = o.notes.notes.iter().collect();
    notes.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
    let specs = hud.notes_schema();
    let editing = !hud.note_draft().id.is_empty();
    let mut form = column![text(if editing { "Edit note" } else { "Add note" })
        .size(typo::TITLE)
        .font(typo::UI_BOLD)
        .color(hud.tokens().text)]
    .spacing(8);
    for spec in specs {
        let id = spec.id;
        let label = spec.label;
        let val = hud.note_draft().field(&id);
        form = form.push(
            text(label.clone())
                .size(typo::META)
                .color(hud.tokens().muted),
        );
        form = form.push(icedtea::widget::themed_text_input(
            label.as_str(),
            val,
            move |v| Message::NoteField {
                id: id.clone(),
                value: v,
            },
            Some(Message::SaveNote),
            hud.tokens(),
            A11y::new(label.clone(), Role::TextBox),
            None,
        ));
    }
    form = form.push(text("Turn").size(typo::META).color(hud.tokens().muted));
    form = form.push(
        container(icedtea::widget::themed_text_input(
            "session",
            &hud.note_draft().turn_index,
            Message::NoteTurn,
            Some(Message::SaveNote),
            hud.tokens(),
            A11y::new("Turn", Role::TextBox),
            None,
        ))
        .width(Length::Fixed(120.0)),
    );
    if !hud.note_draft().event_index.is_empty() {
        form = form.push(
            text(format!("Event #{}", hud.note_draft().event_index))
                .size(typo::META)
                .color(hud.tokens().muted),
        );
    }
    let save_label = if hud.note_saving() {
        "Saving…"
    } else if editing {
        "Save"
    } else {
        "Save note"
    };
    let mut actions = vec![icedtea::action::Action::new(
        "note.save",
        save_label,
        Message::SaveNote,
    )];
    if editing {
        let nid = hud.note_draft().id.clone();
        let del = if hud.note_delete_armed() == nid {
            "Delete?"
        } else {
            "Delete"
        };
        actions.push(icedtea::action::Action::new(
            "note.delete",
            del,
            Message::RequestDelete(nid),
        ));
        actions.push(icedtea::action::Action::new(
            "note.new",
            "New note",
            Message::ResetDraft,
        ));
    }
    form = form.push(card_actions(actions, hud.tokens()));

    let rev = o.notes.revision.clone();
    let mut col = column![
        form,
        text(format!(
            "{} note{}{}",
            notes.len(),
            if notes.len() == 1 { "" } else { "s" },
            if rev.is_empty() {
                String::new()
            } else {
                format!(" · rev {}", rev.chars().take(12).collect::<String>())
            }
        ))
        .size(typo::META)
        .color(hud.tokens().muted)
    ]
    .spacing(12);
    if notes.is_empty() {
        col = col.push(
            text("No notes yet.")
                .size(typo::BODY)
                .color(hud.tokens().muted),
        );
    } else {
        for n in notes {
            let id = n.id.clone();
            let (title, body, extras) = note_fields_view(&n.fields);
            let heading = if title.is_empty() {
                "Empty note".into()
            } else {
                title
            };
            let open = hud.note_expanded(&id);
            let child = if open {
                note_body(hud, n, &body, extras)
            } else {
                prompt_face(&body, hud.tokens())
            };
            col = col.push(expand_card(
                heading,
                child,
                open,
                {
                    let id = id.clone();
                    move |next| Message::NoteExpand {
                        id: id.clone(),
                        open: next,
                    }
                },
                hud.tokens(),
            ));
        }
    }
    col.into()
}

fn tone_color(tone: &str, tok: icedtea::theme::Tokens) -> Color {
    match tone {
        "awaiting" => tok.warning,
        "running" => tok.success,
        "complete" => tok.primary,
        "ending" => tok.accent,
        "cancelled" => tok.danger,
        _ => tok.muted,
    }
}

fn paired_tool<'a>(hud: &'a Hud, ev: &'a TimelineEvent) -> (&'a TimelineEvent, &'a TimelineEvent) {
    let id = ev.tool_call_id.trim();
    if id.is_empty() {
        return (ev, ev);
    }
    let mut call = ev;
    let mut result = ev;
    for other in hud.timeline_events() {
        if other.tool_call_id != ev.tool_call_id {
            continue;
        }
        if other.kind == "tool" || other.event_type == "tool_call" {
            call = other;
        }
        if other.kind == "tool_result"
            || other.event_type == "tool_call_update"
            || other.event_type == "tool_result"
        {
            result = other;
        }
    }
    (call, result)
}

fn inspect_fields(call: &TimelineEvent) -> Vec<ToolField> {
    if !call.tool_fields.is_empty() {
        return call
            .tool_fields
            .iter()
            .map(|f| ToolField {
                id: f.id.clone(),
                label: f.label.clone(),
                value: f.value.clone(),
            })
            .collect();
    }
    tool_fields_from_raw(&call.tool_name, &call.raw_input, 8_000)
}

fn event_payload<'a>(ev: &'a TimelineEvent, selected: bool, hud: &'a Hud) -> Element<'a, Message> {
    let kind = ev.kind.clone();
    let tool = ev.tool_name.clone();
    let preview = ev.preview.clone();
    let content = ev.content.clone();
    let raw_body = timeline_body_text(&preview, &content, selected, 240);
    let body = sanitize_console_text(&display_tool_output(&raw_body, &tool));
    let tok = hud.tokens();
    if !selected {
        return render_payload_text(&body, &kind, hud, false, &ExtractKey::Event(ev.index).id());
    }
    let mut col = column![].spacing(8);
    let family = ev.tool_family.clone();
    let call_id = ev.tool_call_id.clone();
    if !tool.is_empty() || !family.is_empty() || !call_id.is_empty() {
        let mut bits = vec![];
        if !tool.is_empty() {
            bits.push(tool.clone());
        }
        if !family.is_empty() {
            bits.push(family);
        }
        if !call_id.is_empty() {
            bits.push(call_id);
        }
        col = col.push(
            text(bits.join(" · "))
                .size(typo::META)
                .color(tok.muted)
                .font(typo::MONO),
        );
    }
    if kind == "tool" || kind == "tool_result" {
        let (call, result) = paired_tool(hud, ev);
        let fields = inspect_fields(call);
        if !fields.is_empty() {
            col = col.push(text("Input").size(typo::META).color(tok.muted));
            for field in fields {
                col = col.push(
                    text(format!("{}:", field.label))
                        .size(typo::META)
                        .color(tok.muted),
                );
                col = col.push(field_body(
                    hud,
                    &format!("event.{}.in.{}", ev.index, field.id),
                    &field.id,
                    &field.value,
                ));
            }
        }
        let out_tool = if result.tool_name.is_empty() {
            tool.as_str()
        } else {
            result.tool_name.as_str()
        };
        let out_body = sanitize_console_text(&display_tool_output(&result.content, out_tool));
        let img = if !result.image_path.is_empty() {
            result.image_path.clone()
        } else {
            image_result_path(&result.content)
        };
        if !img.is_empty() {
            col = col.push(text("Output").size(typo::META).color(tok.muted));
            col = col.push(icedtea::widget::meta(
                img.clone(),
                hud.tokens(),
                A11y::new(img.clone(), Role::Status),
            ));
            col = col.push(tool_image(&img, hud.tokens()));
        } else if !out_body.trim().is_empty() {
            col = col.push(text("Output").size(typo::META).color(tok.muted));
            col = col.push(render_payload_text(
                &out_body,
                &result.kind,
                hud,
                true,
                &format!("event.{}.out", ev.index),
            ));
        }
    } else if selected {
        col = col.push(selectable(
            hud,
            ExtractKey::Event(ev.index),
            &body,
            hud.tokens(),
            icedtea::typo::FontFace::Ui,
        ));
    } else {
        col = col.push(render_payload_text(
            &body,
            &kind,
            hud,
            true,
            &ExtractKey::Event(ev.index).id(),
        ));
    }
    col.into()
}

fn field_body<'a>(
    hud: &'a Hud,
    bind_id: &str,
    field_id: &str,
    value: &str,
) -> Element<'a, Message> {
    let tea = hud.tokens();
    let body = if field_id == "old_string"
        || field_id == "new_string"
        || field_id == "command"
        || field_id == "pattern"
        || crate::format::looks_like_json(value)
    {
        code_inset(hud, bind_id, tea)
    } else {
        select_bound(
            hud,
            bind_id.to_string(),
            value,
            tea,
            icedtea::typo::FontFace::Mono,
        )
    };
    container(body)
        .padding(8)
        .width(Length::Fill)
        .style(move |_| icedtea::style::card(tea, false))
        .into()
}

fn render_payload_text<'a>(
    body: &str,
    kind: &str,
    hud: &'a Hud,
    expanded: bool,
    field_id: &str,
) -> Element<'a, Message> {
    let tok = hud.tokens();
    let trimmed = body.trim();
    let paint = body_paint(kind, trimmed, expanded);
    if paint == BodyPaint::Empty {
        return text("empty").size(typo::META).color(tok.muted).into();
    }
    let max = if expanded { 4_000 } else { 400 };
    let cut = capped_display(body, max);
    if !expanded {
        return text(cut)
            .size(typo::BODY)
            .font(typo::UI)
            .color(tok.muted)
            .into();
    }
    match paint {
        BodyPaint::Json => code_inset(hud, field_id, hud.tokens()),
        BodyPaint::Image => tool_image(trimmed, hud.tokens()),
        _ => {
            let body = select_bound(
                hud,
                field_id.to_string(),
                &cut,
                tok,
                icedtea::typo::FontFace::Ui,
            );
            if kind == "user"
                || kind == "agent"
                || kind == "subagent"
                || paint == BodyPaint::Markdown
            {
                inset_body(body, hud)
            } else {
                body
            }
        }
    }
}

fn inset_body<'a>(inner: Element<'a, Message>, hud: &'a Hud) -> Element<'a, Message> {
    let tea = hud.tokens();
    container(inner)
        .padding(10)
        .width(Length::Fill)
        .style(move |_| icedtea::style::card(tea, false))
        .into()
}

const POP_OUT_PX: f32 = 16.0;

fn jump_control(
    msg: Message,
    _color: Color,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    // Chip, not Canvas: one 16px canvas program per closed card was a real
    // scroll cost with virtual_column remounting rows every frame.
    icedtea::widget::tooltip_wrap(
        chip_btn("→".into(), msg, tea),
        "Go to Timeline",
        tea,
        A11y::button("Go to Timeline"),
    )
}

fn pop_out_control(
    tok: icedtea::theme::Tokens,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    icedtea::widget::tooltip_wrap(
        mouse_area(
            container(
                Canvas::new(PopOutIcon { color: tok.muted })
                    .width(Length::Fixed(POP_OUT_PX))
                    .height(Length::Fixed(POP_OUT_PX)),
            )
            .padding([6, 8]),
        )
        .on_press(Message::PopOutWindow)
        .into(),
        "Open a desktop window",
        tea,
        A11y::new("Pop out", Role::Button),
    )
}

#[derive(Debug, Clone, Copy)]
struct PopOutIcon {
    color: Color,
}

/// Box in the lower-left, arrow leaving toward the upper-right.
fn pop_out_marks(size: f32) -> (Point, Size, Point, Point, Point, Point) {
    let pad = size * 0.16;
    let box_s = size * 0.52;
    let box_tl = Point::new(pad, size - pad - box_s);
    let tail = Point::new(size * 0.46, size * 0.54);
    let tip = Point::new(size - pad, pad);
    let arm = size * 0.26;
    (
        box_tl,
        Size::new(box_s, box_s),
        tail,
        tip,
        Point::new(tip.x - arm, tip.y),
        Point::new(tip.x, tip.y + arm),
    )
}

impl canvas::Program<Message> for PopOutIcon {
    type State = ();

    fn draw(
        &self,
        _state: &Self::State,
        renderer: &Renderer,
        _theme: &Theme,
        bounds: Rectangle,
        _cursor: mouse::Cursor,
    ) -> Vec<canvas::Geometry> {
        let mut frame = canvas::Frame::new(renderer, bounds.size());
        let stroke = canvas::Stroke::default()
            .with_color(self.color)
            .with_width(1.6)
            .with_line_cap(canvas::LineCap::Round)
            .with_line_join(canvas::LineJoin::Round);
        let size = bounds.width.min(bounds.height);
        let (box_tl, box_sz, tail, tip, left, down) = pop_out_marks(size);
        frame.stroke_rectangle(box_tl, box_sz, stroke);
        let arrow = canvas::Path::new(|b| {
            b.move_to(tail);
            b.line_to(tip);
            b.move_to(left);
            b.line_to(tip);
            b.line_to(down);
        });
        frame.stroke(&arrow, stroke);
        vec![frame.into_geometry()]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pop_out_marks_stay_inside_icon() {
        let size = 16.0;
        let (box_tl, box_sz, tail, tip, left, down) = pop_out_marks(size);
        for p in [box_tl, tail, tip, left, down] {
            assert!(p.x >= 0.0 && p.x <= size, "{p:?}");
            assert!(p.y >= 0.0 && p.y <= size, "{p:?}");
        }
        assert!(box_tl.x + box_sz.width <= size);
        assert!(box_tl.y + box_sz.height <= size);
        assert!(tip.x > tail.x && tip.y < tail.y);
    }

    #[test]
    fn closed_faces_are_plain_text_not_markdown() {
        let _ = prompt_face("# heading\n\n**bold**", tea());
        let _ = prompt_face("plain sentence", tea());
        let _ = prompt_face("", tea());
        let _ = closed_turn_face("user said hello", tea());
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        let face = prod
            .split("fn prompt_face")
            .nth(1)
            .expect("prompt_face")
            .split("fn plain_face")
            .next()
            .expect("body");
        assert!(
            !face.contains("md_body"),
            "closed faces must not parse markdown per row"
        );
        assert!(prod.contains("fn plain_face"));
    }

    #[test]
    fn event_title_is_hash_and_time_only() {
        let ev = TimelineEvent {
            index: 12,
            event_type: "user_message_chunk".into(),
            type_label: "user message chunk".into(),
            kind: "user".into(),
            time: "10:32".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title(&ev), "#12 · 10:32");
        let bare = TimelineEvent {
            index: 3,
            kind: "user".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title(&bare), "#3");
        assert_eq!(
            event_type_human(&ev),
            "user message chunk",
            "human type lives on the face with brand color"
        );
    }

    #[test]
    fn turns_tab_is_fixed_cards_with_search() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        let turns = prod
            .split("fn turns_tab")
            .nth(1)
            .expect("turns_tab")
            .split("fn timeline_tab")
            .next()
            .expect("turns body");
        assert!(turns.contains("turn_list_card"));
        assert!(turns.contains("turns_filter"));
        assert!(!turns.contains("expand_card"));
        assert!(!turns.contains("fn turn_body"));
    }

    #[test]
    fn chip_btn_builds_unsized_chip_buttons() {
        let hud = Hud::default();
        let _ = chip_btn("Add note".into(), Message::ResetDraft, tea());
        let _ = chip_btn("f2".into(), Message::SetTab(Tab::Findings), tea());
        let _ = card_chips(
            &hud,
            Some(CardMark {
                findings: 2,
                notes: 1,
                errors: 0,
                first_finding_event: Some(3),
                first_note_id: "n1".into(),
            }),
            Some(Message::ResetDraft),
            None,
        );
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod source");
        let chip = prod
            .split("fn chip_btn")
            .nth(1)
            .expect("chip_btn")
            .split("fn command_end")
            .next()
            .expect("chip_btn body");
        assert!(chip.contains("widget::chip"));
        assert!(chip.contains("Some(msg)"));
        assert!(chip.contains("Variant::Chip"));
        assert!(!chip.contains("themed_button"));
        assert!(!chip.contains("Fixed(22"));
        assert!(!chip.contains("mouse_area"));
    }

    fn tea() -> icedtea::theme::Tokens {
        icedtea::theme::named("dark").tokens
    }

    #[test]
    fn empty_loading_and_select_use_icedtea_status() {
        let _ = empty_sessions(tea());
        let _ = loading_session("sess-1", tea());
        let _ = select_session(tea());
        let _ = status_copy("control socket down · run: groket serve -d", true, tea());
        let _ = status_copy("12 sessions · ready", false, tea());
    }

    #[test]
    fn timeline_filter_and_empty_list_build_from_hud() {
        let hud = Hud::default();
        assert!(hud.sessions().is_empty());
        let _ = timeline_filter(&hud);
        let _ = session_list_at(&hud, 400.0);
        let _ = layout(&hud);
        let src = include_str!("view.rs");
        assert!(
            src.contains("Search all events"),
            "timeline filter keeps search"
        );
        // Search is a second row so pick lists cannot crush the field.
        let filter_src = src
            .split("fn timeline_filter")
            .nth(1)
            .unwrap_or("")
            .split("fn overview_tab")
            .next()
            .unwrap_or("");
        assert!(
            filter_src.contains("column![picks, search]"),
            "search must not share the picks row"
        );
        assert!(
            filter_src.contains("timeline_count_caption"),
            "empty range must not paint a11y name"
        );
        assert!(src.contains("fn detail_tab_bar"), "session-gated tabs");
    }

    #[test]
    fn code_inset_pretty_prints_json_through_icedtea() {
        let mut hud = Hud::default();
        hud.bind_field("code.json", r#"{ "a": 1 }"#);
        hud.bind_field("code.plain", "not json");
        let _ = code_inset(&hud, "code.json", tea());
        let _ = code_inset(&hud, "code.plain", tea());
        let _ = code_inset(&hud, "missing", tea());
    }

    #[test]
    fn tool_image_uses_slot_for_missing_and_present_files() {
        let missing = tool_image("/no/such/groket-hud-image.png", tea());
        let _ = missing;
        let path = std::env::temp_dir().join("groket-hud-tool-image.txt");
        std::fs::write(&path, b"px").expect("temp image stand-in");
        let _ = tool_image(path.to_str().expect("utf8 path"), tea());
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn session_rail_uses_icedtea_card_list() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod source");
        assert!(prod.contains("widget::list_view("));
        assert!(prod.contains("RowFace::Card"));
        assert!(prod.contains("RowHeights::PerRow"));
        assert!(!prod.contains("fn tea_two_line"));
        assert!(!prod.contains("fn tea_list_view"));
        assert!(prod.contains("SESSION_LIST_W"));
        assert!(prod.contains("pattern::list_detail"));
        assert!(prod.contains("widget::rule_h"));
        assert!(prod.contains("widget::tooltip_wrap"));
        assert!(prod.contains("icedtea::widget::themed_pick_list"));
        assert!(prod.contains("icedtea::widget::themed_text_input"));
        assert!(prod.contains("icedtea::widget::code_block"));
        assert!(prod.contains("icedtea::widget::selectable"));
        // Overview KV uses fixed label gutter + selectable (not value_field()).
        assert!(prod.contains("KV_LABEL_W"));
        assert!(prod.contains("overview_fields"));
        assert!(!prod.contains("widget::value_field("));
        assert!(prod.contains("fn select_bound"));
        assert!(prod.contains("event.{}.in.{}"));
        assert!(prod.contains("icedtea::widget::image_slot"));
        assert!(prod.contains("icedtea::widget::placeholder_skeleton"));
        assert!(prod.contains("icedtea::pattern::status_page"));
        assert!(prod.contains("icedtea::widget::info_bar"));
        assert!(prod.contains("icedtea::widget::markdown_view"));
        assert!(prod.contains("fn expand_card"));
        assert!(prod.contains("fn card_actions"));
        assert!(prod.contains("fn card_chips"));
        assert!(prod.contains("fn command_end"));
        assert!(prod.contains("Add note"));
        // Overview path is selectable; no in-pane Copy path button.
        assert!(!prod.contains("fn overview_commands"));
        assert!(prod.contains("format!(\"f{}\""));
        assert!(prod.contains("format!(\"n{}\""));
        assert!(prod.contains("Tab fields"));
        assert!(prod.contains("Ctrl+1"));
        assert!(prod.contains("Esc"));
        assert!(prod.contains("themed_button("));
        assert!(prod.contains("Variant::Chip"));
        assert!(!prod.contains("themed_button_sized"));
        assert!(prod.contains("fn jump_control"));
        assert!(prod.contains("Go to Timeline"));
        assert!(!prod.contains("struct JumpIcon"));
        assert!(!prod.contains("chip_btn(\"Timeline\""));
        assert!(prod.contains("pattern::command_bar"));
        assert!(prod.contains("pattern::status_bar"));
        assert!(prod.contains("ActionTable"));
        assert!(prod.contains("TURNS_OVERSCAN"));
        assert!(prod.contains("widget::virtual_column"));
        assert!(prod.contains("turns_tab(hud)"));
        assert!(prod.contains("meter: None"));
        assert!(prod.contains("pattern::context_menu"));
        assert!(prod.contains("fn turn_note"));
        assert!(!prod.contains("command_palette_view"));
        assert!(prod.contains("fn event_body"));
        assert!(!prod.contains("time_picker"));
        assert!(!prod.contains("fn drawer"));
        assert!(!prod.contains("fn disclosure"));
        assert!(prod.contains("fn selectable"));
        assert!(prod.contains("fn turn_list_card"));
        assert!(prod.contains("fn closed_turn_face"));
        assert!(prod.contains("Search all events"));
        assert!(prod.contains("Search turns"));
        assert!(!prod.contains("Session events"));
        assert!(prod.contains("fn prompt_face"));
        assert!(!prod.contains("visual_lines("));
        assert!(!prod.contains(".height(height)"));
        assert!(prod.contains("matched in {}:"));
        assert!(prod.contains("brand_role_color"));
        assert!(!prod.contains("accordion_view"));
        assert!(prod.contains("widget::expander"));
        assert!(prod.contains("Peek::Lines(2)"));
        assert!(prod.contains("fn closed_list_card"));
        assert!(!prod.contains("TurnExpand"));
        assert!(!prod.contains("fn turn_body"));
        assert!(prod.contains("FindingExpand"));
        assert!(prod.contains("NoteExpand"));
    }
}
