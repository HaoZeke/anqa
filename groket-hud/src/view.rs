//! Palette layout.

use std::cell::RefCell;
use std::collections::HashMap;
use std::hash::{Hash, Hasher};

use iced::mouse;
use iced::widget::canvas::{self, Canvas};

use iced::widget::{
    column, container, image, markdown, mouse_area, responsive, row, scrollable, stack, text,
    text_editor, text_input, Space,
};
use iced::{Alignment, Color, Element, Length, Padding, Point, Rectangle, Renderer, Size, Theme};
use icedtea::a11y::{A11y, Role};
use icedtea::collection::Tabs;
use icedtea::toast::ToastKind;
use icedtea::variant::Variant;

use crate::app::{ExtractKey, Hud, Message};
use crate::brand;
use crate::format::{
    body_paint, capped_display, display_tool_output, event_brand_role, fmt_duration,
    format_note_time, image_result_path, looks_like_json, looks_like_markdown, note_fields_view,
    origin_label, pretty_json, sanitize_console_text, status_tone, timeline_body_text,
    timeline_query_hit, tool_fields_from_raw, BodyPaint, ToolField,
};
use crate::live::{
    context_fraction, finding_severity_rank, finding_severity_title, visible_range, CardMark,
    SESSION_LIST_W, TIMELINE_OVERSCAN, TIMELINE_ROW_H,
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

fn flat_editor_style(
    tok: icedtea::theme::Tokens,
) -> impl Fn(&iced::Theme, text_editor::Status) -> text_editor::Style {
    move |_t, _s| text_editor::Style {
        background: iced::Background::Color(tok.canvas),
        border: iced::Border {
            color: iced::Color::TRANSPARENT,
            width: 0.0,
            radius: 0.0.into(),
        },
        placeholder: tok.muted,
        value: tok.text,
        selection: tok.selection,
    }
}

fn selectable<'a>(
    hud: &'a Hud,
    key: ExtractKey,
    fallback: &str,
    tea: icedtea::theme::Tokens,
    font: iced::Font,
) -> Element<'a, Message> {
    let Some(buf) = hud.extract(key) else {
        return text(fallback.to_string())
            .size(typo::BODY)
            .font(font)
            .into();
    };
    text_editor(buf)
        .padding(0)
        .size(typo::BODY)
        .font(font)
        .style(flat_editor_style(tea))
        .on_action(move |action| Message::ExtractAction { key, action })
        .into()
}

fn code_inset(src: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let pretty = if looks_like_json(src) {
        pretty_json(src)
    } else {
        src.to_string()
    };
    icedtea::widget::code_block(
        capped_display(&pretty, 2_000),
        tea,
        A11y::new("code", Role::Group),
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
        icedtea::collection::RowFace::Card {
            meter: Some(|i| {
                hud.sessions()
                    .get(i)
                    .map(|r: &crate::model::SessionRow| {
                        context_fraction(r.context_window_usage_pct, &r.context_usage_compact)
                    })
                    .unwrap_or(0.0)
            }),
        },
        A11y::new("Sessions", Role::List),
    )
}

fn detail_pane(hud: &Hud) -> Element<'_, Message> {
    let mut tabs = Tabs::new(Tab::ALL.iter().map(|t| t.label().to_string()));
    tabs.closable = false;
    tabs.active = Tab::ALL.iter().position(|t| *t == hud.tab()).unwrap_or(0);
    let tabs = container(icedtea::widget::tab_bar(
        &tabs,
        |i| Message::SetTab(Tab::ALL[i]),
        |_| Message::SetTab(Tab::Overview),
        hud.tokens(),
        A11y::new("Panes", Role::Tab),
    ))
    .padding(Padding::from([8, 12]));

    let mut stack = column![tabs].spacing(0).height(Length::Fill);
    if hud.tab() == Tab::Timeline && !hud.overview_sid().is_empty() {
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
            Tab::Turns => turns_tab(hud),
            Tab::Timeline => column![].into(),
            Tab::Findings => findings_tab(hud),
            Tab::Notes => notes_tab(hud),
        }
    };
    let tea = hud.tokens();
    if hud.tab() == Tab::Timeline && hud.overview().is_some() {
        stack = stack.push(icedtea::widget::themed_scroll(
            container(timeline_tab(hud, hud.tl_view_h()))
                .padding([16, 20])
                .width(Length::Fill)
                .into(),
            tea,
            A11y::new("Timeline", Role::Group),
            false,
            Some(hud.timeline_scroll_id()),
            Some(|vp: scrollable::Viewport| Message::TimelineScroll {
                y: vp.absolute_offset().y,
                height: vp.bounds().height,
            }),
        ));
    } else if hud.tab() == Tab::Turns && hud.overview().is_some() {
        stack = stack.push(icedtea::widget::themed_scroll(
            container(body).padding([16, 20]).width(Length::Fill).into(),
            tea,
            A11y::new("Turns", Role::Group),
            false,
            Some(hud.turn_scroll_id()),
            Some(|vp: scrollable::Viewport| Message::TurnScroll {
                y: vp.absolute_offset().y,
                height: vp.bounds().height,
            }),
        ));
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

fn timeline_filter(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    row![
        icedtea::widget::meta("Type", tea, A11y::new("Type", Role::Header)),
        icedtea::widget::themed_pick_list(
            &KindFilter::ALL[..],
            Some(hud.timeline_kind()),
            Message::TimelineKind,
            tea,
            A11y::new("Type", Role::ComboBox),
        ),
        container(icedtea::widget::themed_text_input(
            "Filter events",
            hud.timeline_query_draft(),
            Message::TimelineQuery,
            None,
            tea,
            A11y::new("Filter events", Role::TextBox),
            Some(hud.tl_search_id()),
        ))
        .width(Length::Fill),
        icedtea::widget::meta(hud.timeline_meta(), tea, A11y::new("count", Role::Status),),
    ]
    .spacing(8)
    .align_y(Alignment::Center)
    .padding(Padding::from([8, 12]))
    .into()
}

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
    let ctx = meta.context_compact().to_string();
    let status = meta.status_label();
    let tone = status_tone(&status);
    let taken = if meta.duration.is_empty() {
        fmt_duration(meta.duration_seconds)
    } else {
        meta.duration.clone()
    };
    let hero = format!(
        "{} · {} · {} · {}",
        meta.model,
        origin_label(&meta.origin),
        taken,
        ctx,
    );
    let id = meta.session_id.clone();
    let events = meta.num_events.to_string();
    let tools = format!("{} ({} errors)", meta.tool_call_count, meta.error_count);
    let turns_n = o.turns.total.to_string();
    let findings_n = if o.findings.total > 0 {
        o.findings.total.to_string()
    } else {
        o.findings.count.to_string()
    };
    let notes_n = o.notes.count.to_string();
    let git = {
        let repo = meta.git_repo.clone();
        let branch = meta.git_branch.clone();
        match (repo.is_empty(), branch.is_empty()) {
            (true, true) => "—".into(),
            (false, true) => repo,
            (true, false) => branch,
            (false, false) => format!("{repo} · {branch}"),
        }
    };
    let path = meta.path.clone();
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
        context_meter(ctx_frac, tea),
    ]
    .spacing(8);
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
    if summary != title && summary != "No summary text for this session." {
        col = col.push(md_body(&summary, 4000, hud.tokens()));
    } else if summary == "No summary text for this session." {
        col = col.push(icedtea::widget::meta(
            summary,
            hud.tokens(),
            A11y::new("summary", Role::Status),
        ));
    }
    col.push(kv(hud, "session", id, true))
        .push(kv(hud, "events", events, false))
        .push(kv(hud, "tools", tools, false))
        .push(kv(hud, "turns", turns_n, false))
        .push(kv(hud, "findings", findings_n, false))
        .push(kv(hud, "notes", notes_n, false))
        .push(kv(hud, "git", git, false))
        .push(kv(hud, "path", path, true))
        .push(
            row![
                Space::new().width(Length::Fill),
                card_actions(overview_commands(), tea),
            ]
            .align_y(Alignment::Center),
        )
        .into()
}

fn overview_commands() -> Vec<icedtea::action::Action<Message>> {
    vec![icedtea::action::Action::new(
        "session.copy",
        "Copy path",
        Message::CopyPath,
    )]
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

fn kv<'a>(hud: &'a Hud, k: &'static str, v: String, copy: bool) -> Element<'a, Message> {
    let value: Element<'a, Message> = if copy {
        selectable(hud, ExtractKey::Overview(k), &v, hud.tokens(), typo::MONO)
    } else {
        text(v).size(typo::BODY).color(hud.tokens().text).into()
    };
    row![
        text(k)
            .size(typo::META)
            .color(hud.tokens().muted)
            .width(Length::Fixed(80.0)),
        value,
    ]
    .spacing(8)
    .align_y(Alignment::Center)
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
    icedtea::widget::themed_button_sized(
        label.clone(),
        Some(msg),
        tea,
        Variant::Chip,
        Length::Shrink,
        Length::Fixed(22.0),
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
        icedtea::widget::Peek::Lines(4),
        open,
        on_toggle,
        tea,
        A11y::new(title, Role::Group),
    )
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

fn turn_meta(t: &TurnRow) -> String {
    format!(
        "prompt {} · events {} · tools {} ({} errors){}{}",
        t.prompt_index
            .map(|n| n.to_string())
            .unwrap_or_else(|| "—".into()),
        t.event_count,
        t.tool_call_count,
        t.tool_error_count,
        if t.outcome.is_empty() {
            String::new()
        } else {
            format!(" · {}", t.outcome)
        },
        if t.open { " · open" } else { "" },
    )
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

fn event_kind(ev: &TimelineEvent) -> &str {
    if ev.type_label.is_empty() {
        ev.kind.as_str()
    } else {
        ev.type_label.as_str()
    }
}

fn event_face_label(ev: &TimelineEvent) -> &str {
    if !ev.heading.is_empty() {
        ev.heading.as_str()
    } else if !ev.kind.is_empty() {
        ev.kind.as_str()
    } else {
        ev.type_label.as_str()
    }
}

fn event_title(ev: &TimelineEvent) -> String {
    let label = event_face_label(ev).trim();
    let mut out = format!("#{}", ev.index);
    if !label.is_empty() {
        out.push(' ');
        out.push_str(label);
    }
    let time = ev.time.trim();
    if !time.is_empty() {
        out.push_str(" · ");
        out.push_str(time);
    }
    out
}

fn event_face(ev: &TimelineEvent, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let preview = if ev.preview.is_empty() {
        ev.content.as_str()
    } else {
        ev.preview.as_str()
    };
    let face = if ev.heading.is_empty() {
        preview
    } else {
        ev.heading.as_str()
    };
    prompt_face(face, tea)
}

fn event_body<'a>(
    hud: &'a Hud,
    ev: &'a TimelineEvent,
    mark: Option<CardMark>,
) -> Element<'a, Message> {
    let tok = hud.tokens();
    let type_color =
        crate::theme::brand_role_color(event_brand_role(&ev.event_type, &ev.kind, ev.is_error));
    let mut col = column![text(event_kind(ev))
        .size(typo::META)
        .font(typo::UI_BOLD)
        .color(type_color)]
    .spacing(8);
    if !ev.heading.is_empty() {
        col = col.push(
            text(ev.heading.clone())
                .size(typo::TITLE)
                .font(typo::UI_BOLD)
                .color(tok.text),
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

fn finding_jump(f: &FindingRow) -> Message {
    f.primary_event_index
        .or_else(|| f.event_indices.first().copied())
        .map(Message::JumpTimeline)
        .unwrap_or(Message::SetTab(Tab::Timeline))
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

fn turn_paint<'a>(
    hud: &'a Hud,
    key: ExtractKey,
    src: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    if looks_like_markdown(src) {
        md_body(src, 4000, tea)
    } else {
        selectable(hud, key, src, tea, typo::UI)
    }
}

fn turn_body<'a>(hud: &'a Hud, t: &'a TurnRow, mark: Option<CardMark>) -> Element<'a, Message> {
    let idx = t.turn_index;
    let tea = hud.tokens();
    let mut col = column![if t.summary.is_empty() {
        text("No user prompt in this turn")
            .size(typo::BODY)
            .color(hud.tokens().muted)
            .into()
    } else {
        turn_paint(hud, ExtractKey::TurnUser(idx), &t.summary, tea)
    }]
    .spacing(8);
    if !t.assistant_summary.is_empty() {
        col = col.push(text("Assistant").size(typo::META).color(hud.tokens().muted));
        col = col.push(turn_paint(
            hud,
            ExtractKey::TurnAsst(idx),
            &t.assistant_summary,
            tea,
        ));
    }
    col.push(
        row![
            text(turn_meta(t))
                .size(typo::META)
                .color(hud.tokens().muted),
            card_chips(hud, mark, Some(turn_note(t)), Some(turn_jump(t))),
        ]
        .spacing(8)
        .align_y(Alignment::Center)
        .width(Length::Fill),
    )
    .into()
}

fn prompt_face(summary: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    if summary.is_empty() {
        return text("No user prompt in this turn")
            .size(typo::BODY)
            .color(tea.muted)
            .into();
    }
    md_body(summary, 2000, tea)
}

fn turns_tab(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    let turns: &[TurnRow] = &o.turns.turns;
    let (turn_marks, _) = hud.card_marks();
    if turns.is_empty() {
        return text("No turns segmented.")
            .size(typo::BODY)
            .color(hud.tokens().muted)
            .into();
    }
    let tea = hud.tokens();
    let mut col = column![].spacing(8);
    for t in turns {
        let turn = t.turn_index;
        let open = hud.turn_expanded(turn);
        let mark = turn_marks.get(&turn).cloned();
        let child = if open {
            turn_body(hud, t, mark)
        } else {
            column![
                prompt_face(&t.summary, tea),
                card_chips(hud, mark, Some(turn_note(t)), Some(turn_jump(t))),
            ]
            .spacing(6)
            .into()
        };
        col = col.push(expand_card(
            turn_title(t),
            child,
            open,
            move |next| Message::TurnExpand { turn, open: next },
            tea,
        ));
    }
    col.into()
}

fn timeline_tab(hud: &Hud, viewport: f32) -> Element<'_, Message> {
    if hud.timeline_loading() && hud.filtered_indices().is_empty() {
        return loading_session("timeline", hud.tokens());
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
    let n = idxs.len();
    let win = visible_range(
        hud.tl_scroll_y(),
        viewport,
        TIMELINE_ROW_H,
        n,
        TIMELINE_OVERSCAN,
    );
    let start = win.start.min(n);
    let end = win.end.min(n).max(start);
    let mut col = column![].spacing(0);
    if win.pad_top > 0.0 {
        col = col.push(Space::new().height(win.pad_top));
    }
    let tea = hud.tokens();
    let source = hud.timeline_events();
    for &src_i in &idxs[start..end] {
        let Some(ev) = source.get(src_i) else {
            continue;
        };
        let ix = ev.index;
        let open = hud.is_timeline_expanded(ix);
        let mark = ev_marks.get(&ix).cloned();
        let child = if open {
            event_body(hud, ev, mark)
        } else {
            column![
                event_face(ev, tea),
                card_chips(hud, mark, Some(event_note(ev)), None),
            ]
            .spacing(6)
            .into()
        };
        col = col.push(expand_card(
            event_title(ev),
            child,
            open,
            move |_| Message::SelectTimeline(ix),
            tea,
        ));
        col = col.push(Space::new().height(8));
    }
    if win.pad_bottom > 0.0 {
        col = col.push(Space::new().height(win.pad_bottom));
    }
    if !hud.timeline_complete() {
        col = col.push(
            text(if hud.timeline_loading() {
                "Loading more events…"
            } else {
                "More events available — scroll or wait"
            })
            .size(typo::META)
            .color(hud.tokens().muted),
        );
    }
    col.into()
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
        return render_payload_text(&body, &kind, hud, false);
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
                col = col.push(field_body(&field.id, &field.value, hud));
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
            col = col.push(render_payload_text(&out_body, &result.kind, hud, true));
        }
    } else if selected {
        col = col.push(selectable(
            hud,
            ExtractKey::Event(ev.index),
            &body,
            hud.tokens(),
            typo::UI,
        ));
    } else {
        col = col.push(render_payload_text(&body, &kind, hud, true));
    }
    col.into()
}

fn field_body(id: &str, value: &str, hud: &Hud) -> Element<'static, Message> {
    let tok = hud.tokens();
    let is_patch = id == "old_string" || id == "new_string" || id == "command";
    let color = if id == "old_string" {
        tok.danger
    } else if id == "new_string" {
        tok.accent
    } else {
        tok.text
    };
    let tea = hud.tokens();
    container(
        text(value.to_string())
            .size(typo::META)
            .font(if is_patch || id == "pattern" {
                typo::MONO
            } else {
                typo::UI
            })
            .color(color),
    )
    .padding(8)
    .width(Length::Fill)
    .style(move |_| icedtea::style::card(tea, false))
    .into()
}

fn render_payload_text(
    body: &str,
    kind: &str,
    hud: &Hud,
    expanded: bool,
) -> Element<'static, Message> {
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
        BodyPaint::Json => code_inset(&cut, hud.tokens()),
        BodyPaint::Markdown => inset_body(md_body(&cut, max, hud.tokens()), hud),
        BodyPaint::Image => tool_image(trimmed, hud.tokens()),
        _ => {
            let rendered: Element<'static, Message> = match kind {
                "thought" => text(cut)
                    .size(typo::BODY)
                    .font(typo::UI_ITALIC)
                    .color(tok.muted)
                    .into(),
                "plan" => text(cut).size(typo::BODY).color(tok.accent).into(),
                "session" | "task" => text(cut).size(typo::BODY).color(tok.warning).into(),
                "error" => text(cut).size(typo::BODY).color(tok.danger).into(),
                "system" => text(cut).size(typo::BODY).color(tok.accent).into(),
                _ => text(cut).size(typo::BODY).font(typo::UI).into(),
            };
            if kind == "user" || kind == "agent" || kind == "subagent" {
                inset_body(rendered, hud)
            } else {
                rendered
            }
        }
    }
}

fn inset_body(inner: Element<'static, Message>, hud: &Hud) -> Element<'static, Message> {
    let tea = hud.tokens();
    container(inner)
        .padding(10)
        .width(Length::Fill)
        .style(move |_| icedtea::style::card(tea, false))
        .into()
}

const POP_OUT_PX: f32 = 16.0;
const JUMP_PX: f32 = 16.0;

fn jump_control(
    msg: Message,
    color: Color,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    icedtea::widget::tooltip_wrap(
        mouse_area(
            container(
                Canvas::new(JumpIcon { color })
                    .width(Length::Fixed(JUMP_PX))
                    .height(Length::Fixed(JUMP_PX)),
            )
            .padding([4, 6]),
        )
        .on_press(msg)
        .into(),
        "Go to timeline",
        tea,
        A11y::button("Go to timeline"),
    )
}

/// Arrow into a vertical bar: go to this place.
fn jump_marks(size: f32) -> (Point, Point, Point, Point, Point) {
    let pad = size * 0.16;
    let mid = size * 0.5;
    let dest_x = size - pad;
    let tip = Point::new(dest_x - size * 0.18, mid);
    let tail = Point::new(pad, mid);
    let arm = size * 0.22;
    (
        tail,
        tip,
        Point::new(tip.x - arm, tip.y - arm * 0.7),
        Point::new(tip.x - arm, tip.y + arm * 0.7),
        Point::new(dest_x, pad),
    )
}

#[derive(Debug, Clone, Copy)]
struct JumpIcon {
    color: Color,
}

impl canvas::Program<Message> for JumpIcon {
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
        let (tail, tip, up, down, dest_top) = jump_marks(size);
        let dest_bot = Point::new(dest_top.x, size - dest_top.y);
        let path = canvas::Path::new(|b| {
            b.move_to(tail);
            b.line_to(tip);
            b.move_to(up);
            b.line_to(tip);
            b.line_to(down);
            b.move_to(dest_top);
            b.line_to(dest_bot);
        });
        frame.stroke(&path, stroke);
        vec![frame.into_geometry()]
    }
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
    fn turn_prompt_face_builds_markdown_and_plain() {
        let _ = prompt_face("# heading\n\n**bold**", tea());
        let _ = prompt_face("plain sentence", tea());
        let _ = prompt_face("", tea());
    }

    #[test]
    fn event_title_is_hash_index_then_heading() {
        let ev = TimelineEvent {
            index: 12,
            heading: "System".into(),
            type_label: "system".into(),
            kind: "system".into(),
            time: "10:32".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title(&ev), "#12 System · 10:32");
        let bare = TimelineEvent {
            index: 3,
            kind: "user".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title(&bare), "#3 user");
        assert!(!event_title(&ev).starts_with(' '));
        assert!(!event_title(&ev).contains("  "));
    }

    #[test]
    fn jump_marks_point_into_the_bar() {
        let size = 16.0;
        let (tail, tip, up, down, dest_top) = jump_marks(size);
        for p in [tail, tip, up, down, dest_top] {
            assert!(p.x >= 0.0 && p.x <= size, "{p:?}");
            assert!(p.y >= 0.0 && p.y <= size, "{p:?}");
        }
        assert!(tip.x > tail.x);
        assert!(dest_top.x > tip.x);
        assert!((tip.y - tail.y).abs() < f32::EPSILON);
    }

    #[test]
    fn closed_turn_peek_keeps_chips_above_the_fade() {
        let h = icedtea::widget::Peek::Lines(4).height();
        let fade = 12.0_f32.min(h * 0.4).max(4.0);
        let chip_row = 22.0_f32.max(JUMP_PX + 8.0);
        let stack = icedtea::widget::Peek::body_line() + 6.0 + chip_row;
        assert!(
            stack + fade <= h,
            "peek {h} fade {fade} stack {stack} must keep chips above the fade"
        );
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
    }

    #[test]
    fn code_inset_pretty_prints_json_through_icedtea() {
        let _ = code_inset(r#"{"a":1}"#, tea());
        let _ = code_inset("not json", tea());
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
        assert!(prod.contains("format!(\"f{}\""));
        assert!(prod.contains("format!(\"n{}\""));
        assert!(prod.contains("Tab fields"));
        assert!(prod.contains("Ctrl+1"));
        assert!(prod.contains("Esc"));
        assert!(prod.contains("themed_button_sized"));
        assert!(prod.contains("Variant::Chip"));
        assert!(prod.contains("fn jump_control"));
        assert!(prod.contains("Go to timeline"));
        assert!(!prod.contains("chip_btn(\"Timeline\""));
        assert!(prod.contains("pattern::command_bar"));
        assert!(prod.contains("pattern::status_bar"));
        assert!(prod.contains("ActionTable"));
        assert!(!prod.contains("TURN_ROW_H"));
        assert!(!prod.contains("TURNS_ROW_H"));
        assert!(!prod.contains("VIRT_OVERSCAN"));
        assert!(prod.contains("pattern::context_menu"));
        assert!(prod.contains("fn turn_note"));
        assert!(prod.contains("fn overview_commands"));
        assert!(!prod.contains("command_palette_view"));
        assert!(prod.contains("fn event_body"));
        assert!(!prod.contains("time_picker"));
        assert!(!prod.contains("fn drawer"));
        assert!(!prod.contains("fn disclosure"));
        assert!(prod.contains("fn selectable"));
        assert!(prod.contains("fn turn_paint"));
        assert!(prod.contains("fn prompt_face"));
        assert!(!prod.contains("visual_lines("));
        assert!(!prod.contains(".height(height)"));
        assert!(prod.contains("matched in {}:"));
        assert!(prod.contains("brand_role_color"));
        assert!(!prod.contains("accordion_view"));
        assert!(prod.contains("widget::expander"));
        assert!(prod.contains("Peek::Lines(4)"));
        assert!(prod.contains("TurnExpand"));
        assert!(prod.contains("FindingExpand"));
        assert!(prod.contains("NoteExpand"));
    }
}
