//! Palette layout.

use std::cell::RefCell;
use std::collections::HashMap;
use std::hash::{Hash, Hasher};

use iced::mouse;
use iced::widget::canvas::{self, Canvas};

use iced::widget::{
    button, column, container, image, markdown, mouse_area, responsive, row, scrollable, stack,
    text, Space,
};
use iced::{Alignment, Color, Element, Length, Padding, Point, Rectangle, Renderer, Size, Theme};
use icedtea::a11y::{A11y, Role};
use icedtea::toast::ToastKind;
use icedtea::variant::Variant;

use crate::app::{ExtractKey, Hud, Message};
use crate::brand;
use crate::format::{
    body_paint_for, capped_display, display_tool_output, event_brand_role, fmt_duration,
    format_note_time, format_tool_display, human_event_type_label, image_result_path,
    is_chat_message, is_tool_identity, list_event_detail, list_status_label, looks_like_markdown,
    message_markdown_source, note_fields_view, origin_label, overview_fields, path_hint_from_raw,
    sanitize_console_text, status_tone, syntax_for_tool_field, syntax_for_tool_output,
    timeline_body_text, timeline_count_caption, timeline_query_hit, tool_brand_role,
    tool_fields_from_raw, BodyPaint, BrandRole, ToolField,
};
use crate::kit;
use crate::live::{
    context_fraction, finding_severity_rank, finding_severity_title, CardMark, TIMELINE_OVERSCAN,
    TURNS_OVERSCAN,
};
use crate::model::{DiffContext, KindFilter, Tab};
use crate::motion::PageLayer;
use crate::typo;
use crate::wire::{FindingRow, NoteRow, TimelineEvent, TurnRow};

fn rule(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    icedtea::widget::rule_h(tea, A11y::new("rule", Role::Separator))
}

fn empty_sessions(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    kit::status_empty("No sessions", "Is groket serve running?", tea)
}

fn no_session_matches(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    kit::status_empty(
        "No matches",
        "Try another query, or clear search for recent sessions.",
        tea,
    )
}

fn loading_session(sid: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    column![
        icedtea::widget::progress(
            0.0,
            None,
            None,
            true,
            tea,
            A11y::new("Loading", Role::Progress),
        ),
        kit::status_empty("Loading", sid.to_string(), tea),
    ]
    .spacing(12)
    .into()
}

fn select_session(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    kit::status_empty(
        "Search for a session",
        "Type above, then Enter or click a match. Search again to switch.",
        tea,
    )
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
            icedtea::widget::FieldOpts::NONE,
            tea,
            A11y::new("follow-up", Role::TextBox),
            Some(hud.follow_id()),
        ))
        .width(Length::Fill),
        icedtea::widget::themed_button(
            "Send follow-up",
            Some(Message::SendFollow),
            tea,
            Variant::Primary,
            icedtea::icon::Icons::NONE,
            A11y::button("Send follow-up"),
        ),
    ]
    .spacing(8)
    .into()
}

#[allow(dead_code)] // kept for footer status chrome; exercised in tests
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
        "running" => Variant::Warning,
        "cancelled" => Variant::Danger,
        _ => Variant::Quiet,
    }
}

fn brand_variant(role: BrandRole) -> Variant {
    match role {
        BrandRole::Complete => Variant::Success,
        BrandRole::Running => Variant::Warning,
        BrandRole::Failed => Variant::Danger,
        BrandRole::Cream => Variant::Primary,
        BrandRole::Cancelled => Variant::Quiet,
    }
}

/// Small icedtea badge for a type or tool name (same face as session status).
fn label_badge(
    label: impl Into<String>,
    role: BrandRole,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let label = label.into();
    icedtea::widget::badge(
        label.clone(),
        None,
        tea,
        brand_variant(role),
        icedtea::widget::BadgeSize::Small,
        A11y::new(label, Role::Status),
    )
}

/// Session / turn / severity status — icedtea ``badge`` (same face everywhere).
fn status_chip(
    label: impl Into<String>,
    tone: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let label = label.into();
    icedtea::widget::badge(
        label.clone(),
        None,
        tea,
        tone_variant(tone),
        icedtea::widget::BadgeSize::Small,
        A11y::new(label, Role::Status),
    )
}

/// Status plus identity chips — Overview, Recent cards, and the browse bar.
fn session_state_row(
    status: &str,
    model: &str,
    origin: &str,
    duration: &str,
    subagent: bool,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let status_label = if status.trim().is_empty() {
        "—"
    } else {
        status.trim()
    };
    let mut chips = row![status_chip(
        status_label.to_string(),
        status_tone(status_label),
        tea,
    )]
    .spacing(8)
    .align_y(Alignment::Center);
    if subagent {
        chips = chips.push(status_chip(String::from("subagent"), "", tea));
    }
    if !model.trim().is_empty() {
        chips = chips.push(status_chip(model.trim().to_string(), "", tea));
    }
    let origin = origin_label(origin);
    if origin != "—" {
        chips = chips.push(status_chip(origin.to_string(), "", tea));
    }
    if !duration.trim().is_empty() && duration != "—" {
        chips = chips.push(status_chip(duration.trim().to_string(), "", tea));
    }
    chips.into()
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

fn code_inset<'a>(
    hud: &'a Hud,
    id: &str,
    fallback: &str,
    syntax: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    // Prefer the selectable bind buffer; fall back to *fallback* so a missing
    // bind (e.g. first paint before extract) does not paint an empty Code pane.
    let Some(buf) = hud.field(id) else {
        if fallback.is_empty() {
            return text(String::new()).size(typo::META).font(typo::MONO).into();
        }
        return text(fallback.to_string())
            .size(typo::META)
            .font(typo::MONO)
            .into();
    };
    let id = id.to_string();
    // Real iced highlighter (syntect) — not plain mono ``code_block``.
    let lang = if syntax.is_empty() { "txt" } else { syntax };
    icedtea::widget::highlighted_code(
        buf,
        lang,
        move |action| Message::Select {
            id: id.clone(),
            action,
        },
        tea,
        hud.theme_name(),
        Length::Shrink,
        A11y::new("code", Role::TextBox),
    )
}

pub fn layout(hud: &Hud) -> Element<'_, Message> {
    let tok = hud.tokens();
    let tea = hud.tokens();
    let mut search = row![
        icedtea::widget::tooltip_wrap(
            mouse_area(
                image(brand::chrome_handle(crate::theme::canvas_is_dark(tok)))
                    .width(brand::chrome_width())
                    .height(brand::chrome_height()),
            )
            .on_press(Message::SessionsHome)
            .into(),
            "Session list",
            icedtea::widget::TooltipAnchor::Follow,
            tea,
            A11y::button("Session list"),
        ),
        kit::search_field(
            "Search sessions",
            hud.query(),
            Message::SearchChanged,
            Some(Message::ActivateSelected),
            tea,
            A11y::new("Search sessions", Role::TextBox),
            Some(hud.search_id()),
        ),
    ]
    .spacing(12)
    .align_y(Alignment::Center);
    if !hud.window_mode() {
        search = search.push(pop_out_control(tok, tea));
    }
    let search = search.padding(Padding::from([12, 16]));

    // Spotlight: search → pick → full-width browse. Type again to switch.
    let body: Element<'_, Message> = {
        let inner = if hud.browse_mode() {
            detail_pane(hud)
        } else {
            session_picker(hud)
        };
        if hud.page_layer() == PageLayer::Browse && hud.page_moving() {
            icedtea::motion::overlay(
                inner,
                hud.page_progress(),
                hud.page_slide(),
                tea,
                A11y::new("browse", Role::Group),
            )
        } else {
            inner
        }
    };

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
    // Always stack the shell so opening the context menu does not remount
    // selectable editors (iced only paints a selection while they stay focused).
    let mut layers = stack![busy];
    if let Some(origin) = hud.context_origin() {
        layers = layers.push(icedtea::pattern::context_menu(
            hud.context_actions(),
            origin,
            hud.window_size(),
            Message::ContextDismiss,
            1.0,
            tea,
        ));
    }
    let scene = layers.into();
    if hud.help_open() {
        return fade_palette(
            kit::help_modal(
                scene,
                &crate::help::help_table_for(hud.key_scope(), hud.key_overlay()),
                tea,
            ),
            hud,
            tea,
        );
    }
    fade_palette(scene, hud, tea)
}

fn fade_palette<'a>(
    child: Element<'a, Message>,
    hud: &Hud,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    // icedtea OverlayLayer does not forward Widget::overlay, so pick lists
    // never open while this wrapper is mounted. Tokens::fade already paints
    // the show/hide. Keep the layer only while the fade is running.
    if !hud.overlay_moving() {
        return child;
    }
    icedtea::motion::overlay(
        child,
        hud.overlay_progress(),
        icedtea::motion::Slide::Up,
        tea,
        A11y::new("palette", Role::Group),
    )
}

fn page_body<'a>(
    child: Element<'a, Message>,
    hud: &Hud,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    if hud.page_layer() != PageLayer::Pane || !hud.page_moving() {
        return container(child)
            .width(Length::Fill)
            .height(Length::Fill)
            .into();
    }
    container(icedtea::motion::overlay(
        child,
        hud.page_progress(),
        hud.page_slide(),
        tea,
        A11y::new("page", Role::Group),
    ))
    .width(Length::Fill)
    .height(Length::Fill)
    .clip(true)
    .into()
}

/// Full-width session matches (Spotlight results). No permanent left rail.
fn session_picker(hud: &Hud) -> Element<'_, Message> {
    responsive(move |size| session_picker_at(hud, size.height.max(1.0))).into()
}

fn session_picker_at(hud: &Hud, viewport: f32) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    let idle = hud.query().trim().is_empty();
    if hud.sessions().is_empty() {
        if idle {
            // Catalog empty vs still loading — same honest empty; no full dump.
            return if hud.catalog_busy() {
                loading_session("sessions", tea)
            } else {
                empty_sessions(tea)
            };
        }
        return no_session_matches(tea);
    }
    let mut window = hud.list_window();
    window.viewport = viewport.max(1.0);
    let rows = hud.sessions();
    let selected = hud.list_selection().primary();
    let list = icedtea::widget::virtual_column(
        hud.session_heights(),
        window,
        1,
        selected,
        Message::ListScroll,
        Some(hud.list_scroll_id()),
        tea,
        move |i| {
            let Some(row) = rows.get(i) else {
                return Space::new().height(0).into();
            };
            session_list_card(row, i, selected == Some(i), tea)
        },
        A11y::new("Sessions", Role::List),
    );
    if idle {
        // Spotlight: Recent strip (grows as the list is paged).
        return column![
            icedtea::widget::meta("Recent", tea, A11y::new("Recent", Role::Header),),
            list,
        ]
        .spacing(8)
        .padding(Padding::from([8, 12]))
        .height(Length::Fill)
        .into();
    }
    container(list)
        .padding(Padding::from([8, 12]))
        .height(Length::Fill)
        .into()
}

fn session_list_card(
    row: &crate::model::SessionRow,
    index: usize,
    selected: bool,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let status = row.status_label();
    let taken = if row.duration_seconds > 0.0 {
        fmt_duration(row.duration_seconds)
    } else {
        String::new()
    };
    let title = text(row.display_title().to_string())
        .size(typo::BODY)
        .font(if selected { typo::UI_BOLD } else { typo::UI })
        .color(tea.text)
        .width(Length::Fill);
    let mut body = column![
        title,
        session_state_row(&status, &row.model, &row.origin, &taken, false, tea),
    ]
    .spacing(4)
    .width(Length::Fill);
    let ctx = row.context_usage_compact.trim();
    if !ctx.is_empty() {
        body = body.push(text(ctx.to_string()).size(typo::META).color(tea.muted));
    }
    column![
        mouse_area(
            container(body)
                .padding(Padding {
                    top: 8.0,
                    right: 12.0,
                    bottom: 8.0,
                    left: 12.0,
                })
                .width(Length::Fill)
                .style(move |_| icedtea::style::card(tea, selected)),
        )
        .on_press(Message::SelectSession(index)),
        Space::new().height(crate::live::LIST_CARD_GAP),
    ]
    .into()
}

fn detail_pane(hud: &Hud) -> Element<'_, Message> {
    let session_ready = hud.overview().is_some() || !hud.overview_pending().is_empty();
    let tea = hud.tokens();
    let tabs = container(kit::pane_tabs(
        hud.tab(),
        session_ready,
        hud.visible_tabs(),
        tea,
    ))
    .padding(Padding::from([4, 12]));

    let mut stack = column![].spacing(0).height(Length::Fill);
    if let Some(bar) = browse_session_bar(hud, tea) {
        stack = stack.push(bar);
    }
    stack = stack.push(tabs);
    // List filters stay off while reading a full-pane event.
    if hud.tab() == Tab::Timeline && hud.overview().is_some() && hud.timeline_open().is_none() {
        stack = stack.push(timeline_filter(hud));
    }
    let body: Element<'_, Message> = if hud.overview().is_none() {
        if !hud.overview_pending().is_empty() {
            loading_session(hud.overview_pending(), hud.body_tokens())
        } else {
            select_session(hud.body_tokens())
        }
    } else {
        match hud.tab() {
            Tab::Overview => overview_tab(hud),
            Tab::Turns | Tab::Timeline | Tab::Diff => column![].into(),
            Tab::Findings => findings_tab(hud),
            Tab::Notes => notes_tab(hud),
        }
    };
    if hud.tab() == Tab::Timeline && hud.overview().is_some() {
        stack = stack.push(page_body(
            container(timeline_tab(hud))
                .padding([8, 12])
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
            hud,
            tea,
        ));
    } else if hud.tab() == Tab::Turns && hud.overview().is_some() {
        stack = stack.push(page_body(
            container(turns_tab(hud))
                .padding([8, 12])
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
            hud,
            tea,
        ));
    } else if hud.tab() == Tab::Diff && hud.overview().is_some() {
        stack = stack.push(page_body(
            container(diff_tab(hud))
                .padding(Padding {
                    top: 0.0,
                    right: 12.0,
                    bottom: 8.0,
                    left: 12.0,
                })
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
            hud,
            tea,
        ));
    } else {
        stack = stack.push(page_body(
            icedtea::widget::themed_scroll(
                container(body).padding([16, 20]).width(Length::Fill).into(),
                tea,
                A11y::new("Detail", Role::Group),
                false,
                None,
                None::<fn(scrollable::Viewport) -> Message>,
            ),
            hud,
            tea,
        ));
    }
    container(stack)
        .width(Length::Fill)
        .height(Length::Fill)
        .into()
}

/// Session identity under the search bar while browsing (no left rail).
fn browse_session_bar<'a>(
    hud: &'a Hud,
    tea: icedtea::theme::Tokens,
) -> Option<Element<'a, Message>> {
    let title = if let Some(o) = hud.overview() {
        let t = o.meta.title.trim();
        if t.is_empty() {
            let l = o.meta.label.trim();
            if l.is_empty() {
                hud.overview_sid().to_string()
            } else {
                l.to_string()
            }
        } else {
            t.to_string()
        }
    } else if !hud.overview_pending().is_empty() {
        hud.overview_pending().to_string()
    } else {
        return None;
    };
    let status = if let Some(o) = hud.overview() {
        let s = o.meta.status_label();
        if s.is_empty() {
            String::new()
        } else {
            s
        }
    } else {
        "Loading…".into()
    };
    let mut row = row![text(title)
        .size(typo::BODY)
        .font(typo::UI_BOLD)
        .color(tea.text),]
    .spacing(10)
    .align_y(Alignment::Center)
    .width(Length::Fill);
    if let Some(o) = hud.overview() {
        let taken = if o.meta.duration.is_empty() {
            fmt_duration(o.meta.duration_seconds)
        } else {
            o.meta.duration.clone()
        };
        row = row.push(session_state_row(
            &status,
            &o.meta.model,
            &o.meta.origin,
            &taken,
            o.meta.is_subagent(),
            tea,
        ));
    } else if !status.is_empty() {
        row = row.push(session_state_row(&status, "", "", "", false, tea));
    }
    row = row.push(Space::new().width(Length::Fill));
    row = row.push(
        text("Search again to switch")
            .size(typo::META)
            .color(tea.muted),
    );
    Some(
        container(row)
            .padding(Padding::from([6, 16]))
            .width(Length::Fill)
            .into(),
    )
}

fn timeline_filter(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    // Two rows: picks + optional range; full-width search below so it never
    // shares width with Turn/Filter (one-row bar clipped or overlapped the field).
    let mut picks = row![].spacing(8).align_y(Alignment::Center);
    if !hud.hide_events_turn_pick() {
        picks = picks.push(icedtea::widget::meta(
            "Turn",
            tea,
            A11y::new("Turn", Role::Header),
        ));
        picks = picks.push(kit::compact_pick(
            hud.events_turn_options(),
            Some(hud.events_turn_selected()),
            Message::EventsTurnPicked,
            tea,
            A11y::new("Turn", Role::ComboBox),
        ));
    }
    picks = picks.push(icedtea::widget::meta(
        "Filter",
        tea,
        A11y::new("Filter", Role::Header),
    ));
    picks = picks.push(kit::compact_pick(
        &KindFilter::ALL[..],
        Some(hud.timeline_kind()),
        Message::TimelineKind,
        tea,
        A11y::new("Filter", Role::ComboBox),
    ));
    if hud.show_timeline_tail() {
        picks = picks.push(icedtea::widget::themed_switch(
            "Tail",
            hud.timeline_follow_tail(),
            Message::TimelineTail,
            tea,
            A11y::new("Tail", Role::Switch).with_checked(hud.timeline_follow_tail()),
        ));
    }
    picks = picks
        .push(Space::new().width(Length::Fill))
        .width(Length::Fill);
    if let Some(cap) = timeline_count_caption(&hud.timeline_meta()) {
        picks = picks.push(icedtea::widget::meta(
            cap.to_string(),
            tea,
            A11y::new(cap.to_string(), Role::Status),
        ));
    }
    let search = container(kit::search_field(
        "Search events…",
        hud.timeline_query_draft(),
        Message::TimelineQuery,
        None,
        tea,
        A11y::new("Search events", Role::TextBox),
        Some(hud.tl_search_id()),
    ))
    .width(Length::Fill);
    column![picks, search]
        .spacing(10)
        .width(Length::Fill)
        .padding(Padding::from([8, 12]))
        .into()
}

fn overview_tab(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    let o = hud.overview().unwrap();
    let meta = &o.meta;
    let mut title = meta.title.clone();
    if title.is_empty() {
        title = hud.overview_sid().to_string();
    }
    if meta.is_subagent() && !title.to_ascii_lowercase().starts_with("subagent") {
        title = format!("Subagent · {title}");
    }
    let mut summary = o.summary.clone();
    if summary.is_empty() {
        summary = meta.summary.clone();
    }
    if summary.is_empty() {
        summary = "No summary text for this session.".into();
    }
    let status = meta.status_label();
    let taken = if meta.duration.is_empty() {
        fmt_duration(meta.duration_seconds)
    } else {
        meta.duration.clone()
    };
    let tok = tea;
    let ctx_frac = context_fraction(meta.context_window_usage_pct, meta.context_compact());
    let status_row = session_state_row(
        &status,
        &meta.model,
        &meta.origin,
        &taken,
        meta.is_subagent(),
        tea,
    );
    let mut col = column![
        text(title.clone())
            .size(typo::TITLE)
            .font(typo::UI_BOLD)
            .color(tok.text),
        status_row,
    ]
    .spacing(8);
    // Progress only where context matters (session detail), and only when known.
    if ctx_frac > 0.0 {
        col = col.push(kit::context_progress(ctx_frac, tea));
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
        return text(cut).size(typo::META).font(typo::UI).into();
    }
    markdown_element(&cut, tea)
}

/// Always markdown (TUI chat messages): hard breaks + icedtea markdown_view.
fn chat_md_body(
    src: &str,
    max_chars: usize,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let prepared = message_markdown_source(src);
    let cut: String = prepared.chars().take(max_chars).collect();
    if cut.trim().is_empty() {
        return text("empty").size(typo::META).color(tea.muted).into();
    }
    markdown_element(&cut, tea)
}

fn markdown_element(src: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    // icedtea markdown_view uses PAGE for H1 and BODY for copy — too big
    // on the overlay. Compact settings; TODO.md tracks an icedtea size.
    iced::widget::markdown::view(intern_md(src), hud_md_settings(tea))
        .map(|url| Message::MdLink(url.to_string()))
}

fn hud_md_settings(tea: icedtea::theme::Tokens) -> iced::widget::markdown::Settings {
    let body = typo::META as f32;
    iced::widget::markdown::Settings {
        text_size: body.into(),
        h1_size: (typo::TITLE as f32).into(),
        h2_size: (typo::BODY as f32).into(),
        h3_size: body.into(),
        h4_size: body.into(),
        h5_size: body.into(),
        h6_size: body.into(),
        code_size: (typo::CODE as f32).into(),
        spacing: (body * 0.75).into(),
        style: hud_md_style(tea),
    }
}

fn hud_md_style(tea: icedtea::theme::Tokens) -> iced::widget::markdown::Style {
    let s = tea.scheme();
    let mut style = iced::widget::markdown::Style::from_palette(iced::theme::Palette {
        background: s.surface,
        text: s.on_surface,
        primary: s.primary,
        success: s.success,
        warning: s.warning,
        danger: s.error,
    });
    style.font = typo::UI;
    style.inline_code_color = s.on_surface;
    style.inline_code_font = typo::MONO;
    style.code_block_font = typo::MONO;
    style.link_color = s.primary;
    style.inline_code_highlight.background = iced::Background::Color(s.surface_container_high);
    style
}

/// One Overview meta row via icedtea value_field / plain labeled readout.
fn kv<'a>(
    hud: &'a Hud,
    key: &'static str,
    label: &'static str,
    v: String,
    copy: bool,
) -> Element<'a, Message> {
    let tea = hud.tokens();
    if copy {
        if let Some(buf) = hud.field(&ExtractKey::Overview(key).id()) {
            let id = ExtractKey::Overview(key).id();
            return kit::labeled_value(
                label,
                buf,
                move |action| Message::Select {
                    id: id.clone(),
                    action,
                },
                icedtea::typo::FontFace::Mono,
                tea,
                A11y::new(key, Role::Group),
            );
        }
    }
    kit::labeled_plain(label, v, tea)
}

fn footer(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    kit::status_footer(
        &crate::help::footer_table_for(hud.key_scope(), hud.key_overlay()),
        tea,
    )
}

fn chip_btn(label: String, msg: Message, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    icedtea::widget::chip(
        label.clone(),
        Some(msg),
        None,
        tea,
        Variant::Chip,
        icedtea::widget::ChipKind::Assist,
        icedtea::icon::Icons::NONE,
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
    // Full-width bar (open cards / forms): marks left, commands right.
    row![
        card_marks_row(hud, mark),
        Space::new().width(Length::Fill),
        card_cmds_row(hud, note, jump, None),
    ]
    .spacing(8)
    .align_y(Alignment::Center)
    .width(Length::Fill)
    .into()
}

/// Compact chips for closed-card title rows (no flex fill — sits beside title).
fn card_chips_inline(
    hud: &Hud,
    mark: Option<CardMark>,
    note: Option<Message>,
    jump: Option<Message>,
    diff: Option<Message>,
) -> Element<'static, Message> {
    row![
        card_marks_row(hud, mark),
        card_cmds_row(hud, note, jump, diff),
    ]
    .spacing(4)
    .align_y(Alignment::Center)
    .into()
}

fn card_marks_row(hud: &Hud, mark: Option<CardMark>) -> Element<'static, Message> {
    let tea = hud.tokens();
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
        // Tool errors are already in turn_stats_row ("N tools · M tool errors").
    }
    marks.into()
}

fn card_cmds_row(
    hud: &Hud,
    note: Option<Message>,
    jump: Option<Message>,
    diff: Option<Message>,
) -> Element<'static, Message> {
    let tea = hud.tokens();
    let tok = hud.tokens();
    let mut cmds = row![].spacing(4);
    if let Some(msg) = note {
        cmds = cmds.push(chip_btn("Add note".into(), msg, tea));
    }
    if let Some(msg) = diff {
        cmds = cmds.push(icedtea::widget::tooltip_wrap(
            chip_btn("Diff".into(), msg, tea),
            "Go to Diff",
            icedtea::widget::TooltipAnchor::Follow,
            tea,
            A11y::button("Go to Diff"),
        ));
    }
    if let Some(msg) = jump {
        cmds = cmds.push(jump_control(msg, tok.muted, tea));
    }
    cmds.into()
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
    progress: f32,
    on_toggle: impl Fn(bool) -> Message + 'a,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    icedtea::widget::expander(
        title.clone(),
        child,
        icedtea::widget::Peek::Lines(2),
        open,
        progress,
        on_toggle,
        tea,
        A11y::new(title, Role::Group),
    )
}

/// Closed Timeline row: flat card. Click opens full-pane detail (not expand).
///
/// Chips share the title row so the virtual height only needs title + face
/// (a third chips row was clipped by ``TIMELINE_ROW_H`` under ``clip(true)``).
fn closed_list_card<'a>(
    title: Element<'a, Message>,
    face: Element<'a, Message>,
    chips: Element<'a, Message>,
    on_open: Message,
    selected: bool,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    let header = row![
        title,
        Space::new().width(Length::Fill),
        chips,
        text("›").size(typo::META).color(tea.muted),
    ]
    .spacing(6)
    .align_y(Alignment::Center)
    .width(Length::Fill);
    let body = column![header, face].spacing(4).width(Length::Fill);
    mouse_area(
        container(body)
            .padding(10)
            .width(Length::Fill)
            .style(move |_| icedtea::style::card(tea, selected)),
    )
    .on_press(on_open)
    .into()
}

fn turn_title(t: &TurnRow) -> String {
    let label = t.face_caption();
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
        status_chip(status, tone, tea),
        icedtea::widget::meta(hero.clone(), tea, A11y::new(hero, Role::Status)),
    ]
    .spacing(8)
    .align_y(Alignment::Center)
    .into()
}

fn turn_run_chips(t: &TurnRow, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    if t.subagent_runs.is_empty() {
        return Space::new().height(0).into();
    }
    let mut col = column![].spacing(2);
    for run in &t.subagent_runs {
        let kind = if run.subagent_type.is_empty() {
            "subagent".to_string()
        } else {
            run.subagent_type.clone()
        };
        let desc = if run.description.is_empty() {
            run.child_session_id.clone()
        } else {
            run.description.clone()
        };
        let label = format!("{kind} · {desc} · {}", run.status);
        let chip = text(label).size(typo::META).color(tea.muted);
        if run.openable {
            col = col.push(mouse_area(chip).on_press(Message::OpenChild {
                path: run.child_path.clone(),
                sid: run.child_session_id.clone(),
            }));
        } else {
            col = col.push(chip);
        }
    }
    col.into()
}

fn turn_note(t: &TurnRow) -> Message {
    Message::StartNote {
        turn: t.face_id().map(|n| n.to_string()).unwrap_or_default(),
        event: String::new(),
    }
}

/// Open Timeline with this turn’s events only (list, not a single-event detail).
fn turn_diff(t: &TurnRow) -> Message {
    Message::OpenTurnDiff {
        prompt_index: t.prompt_index,
    }
}

fn turn_jump(t: &TurnRow) -> Message {
    use crate::model::EventsTurnPick;
    let label = t.face_caption();
    Message::EventsTurnPicked(EventsTurnPick {
        turn_index: Some(t.turn_index),
        label,
    })
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

fn event_card_label(ev: &TimelineEvent) -> String {
    if is_tool_identity(&ev.kind, &ev.event_type, &ev.tool_name) {
        format_tool_display(&ev.tool_name)
    } else {
        event_type_human(ev)
    }
}

fn event_title_meta(ev: &TimelineEvent) -> String {
    let mut bits: Vec<String> = Vec::new();
    if let Some(turn) = ev.turn_index {
        bits.push(format!("turn {turn}"));
    }
    let time = ev.time.trim();
    if !time.is_empty() {
        bits.push(time.to_string());
    }
    bits.join(" · ")
}

/// Human type + brand role for the heading badge next to ``#index``.
fn event_type_paint(ev: &TimelineEvent) -> Option<(String, BrandRole)> {
    let human = event_type_human(ev);
    if human.is_empty() {
        return None;
    }
    Some((
        human,
        event_brand_role(&ev.event_type, &ev.kind, ev.is_error),
    ))
}

fn event_tool_role(ev: &TimelineEvent) -> BrandRole {
    if ev.is_error {
        BrandRole::Failed
    } else {
        tool_brand_role(&ev.tool_name, false).unwrap_or(BrandRole::Cancelled)
    }
}

/// ``#index`` + type badge on one row (turn / time muted after).
fn event_list_heading(
    ev: &TimelineEvent,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let mut head = row![text(format!("#{}", ev.index))
        .size(typo::META)
        .font(typo::UI_BOLD)
        .color(tea.text),]
    .spacing(8)
    .align_y(Alignment::Center);
    if let Some((human, role)) = event_type_paint(ev) {
        head = head.push(label_badge(human, role, tea));
    }
    let meta = event_title_meta(ev);
    if !meta.is_empty() {
        head = head.push(text(format!("· {meta}")).size(typo::META).color(tea.muted));
    }
    head.into()
}

fn event_face(ev: &TimelineEvent, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let tool_row = is_tool_identity(&ev.kind, &ev.event_type, &ev.tool_name);
    let raw_preview = if ev.preview.is_empty() {
        ev.content.as_str()
    } else {
        ev.preview.as_str()
    };
    let raw_preview = if raw_preview.is_empty() {
        ev.heading.as_str()
    } else {
        raw_preview
    };
    let preview = if tool_row {
        list_event_detail(raw_preview, &ev.tool_name)
    } else {
        raw_preview.to_string()
    };
    // One scannable line (TUI type + summary columns), not a markdown stack.
    let preview = capped_display(&plain_card_text(&preview), 160);
    if !tool_row {
        if preview.is_empty() {
            return text("—").size(typo::META).color(tea.muted).into();
        }
        return text(preview).size(typo::META).color(tea.text).into();
    }
    let name = label_badge(format_tool_display(&ev.tool_name), event_tool_role(ev), tea);
    if preview.is_empty() {
        return name;
    }
    row![name, text(preview).size(typo::META).color(tea.text)]
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
    let mut col = column![].spacing(6);
    if !ev.child_session_id.is_empty() {
        let mut line = ev.child_session_id.clone();
        if !ev.subagent_status.is_empty() {
            line = format!("{line} · {}", ev.subagent_status);
        }
        if let Some(ms) = ev.duration_ms {
            line = format!("{line} · {ms} ms");
        }
        col = col.push(text(line).size(typo::META).color(tok.muted));
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
    // ~2 lines at typical detail width; keeps closed-card height honest.
    plain_face(summary, "No user prompt in this turn", 180, tea)
}

/// Closed-card preview only. Markdown parse/layout per visible row was the
/// Turns/Timeline scroll tax; open bodies use selectable / md_body when needed.
fn prompt_face(summary: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    plain_face(summary, "—", 280, tea)
}

/// Strip light markdown so closed cards do not show raw ``**bold**`` markers.
fn plain_card_text(summary: &str) -> String {
    let mut out = String::with_capacity(summary.len());
    let mut chars = summary.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '*' | '_' | '`' => {
                // Drop run of the same marker (**, __, ``` fence ticks).
                while chars.peek() == Some(&c) {
                    chars.next();
                }
            }
            _ => out.push(c),
        }
    }
    out.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn plain_face(
    summary: &str,
    empty: &'static str,
    max_chars: usize,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    if summary.is_empty() {
        return text(empty).size(typo::META).color(tea.muted).into();
    }
    text(capped_display(&plain_card_text(summary), max_chars))
        .size(typo::META)
        .font(typo::UI)
        .color(tea.text)
        .into()
}

/// Fixed Turns card: prompt + light meta + jump/note (no expander / assistant body).
///
/// Title row carries chips so the 2-line prompt is not pushed under the
/// virtual clip (``CLOSED_TURN_CARD_H``).
fn turn_list_card(
    hud: &Hud,
    t: &TurnRow,
    mark: Option<CardMark>,
    selected: bool,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let jump = turn_jump(t);
    let title = text(turn_title(t))
        .size(typo::BODY)
        .font(typo::UI_BOLD)
        .color(tea.text);
    let header = row![
        title,
        Space::new().width(Length::Fill),
        card_chips_inline(
            hud,
            mark,
            Some(turn_note(t)),
            Some(jump.clone()),
            hud.turn_has_diff(t.prompt_index).then(|| turn_diff(t)),
        ),
    ]
    .spacing(6)
    .align_y(Alignment::Center)
    .width(Length::Fill);
    let body = column![
        header,
        turn_stats_row(t, tea),
        turn_run_chips(t, tea),
        closed_turn_face(&t.summary, tea),
    ]
    .spacing(4)
    .width(Length::Fill);
    mouse_area(
        container(body)
            .padding(10)
            .width(Length::Fill)
            .style(move |_| icedtea::style::card(tea, selected)),
    )
    .on_press(jump)
    .into()
}

fn turns_filter(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    container(kit::search_field(
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
    let tea = hud.body_tokens();
    if turns.is_empty() {
        return kit::status_empty("No turns", "Nothing segmented yet.", tea);
    }
    let idxs = hud.filtered_turn_indices();
    let list = if idxs.is_empty() {
        kit::status_empty("No matches", "No turns match this search.", tea)
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
                let selected = hud.turns_focus() == Some(t.turn_index);
                column![
                    turn_list_card(hud, t, mark, selected, tea),
                    Space::new().height(crate::live::LIST_GAP),
                ]
                .into()
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
    if let Some(ix) = hud.timeline_open() {
        return event_detail_pane(hud, ix);
    }
    if hud.timeline_query().trim().is_empty()
        && hud.last_timeline().is_none()
        && hud.filtered_indices().is_empty()
        && !hud.timeline_loading()
    {
        // Should be rare: SetTab/All turns loads immediately. Honest fallback.
        return loading_session("events", hud.body_tokens());
    }
    if hud.timeline_loading() && hud.filtered_indices().is_empty() {
        return loading_session("events", hud.body_tokens());
    }
    let idxs = hud.filtered_indices();
    if idxs.is_empty() {
        if hud.timeline_loading() || !hud.timeline_complete() {
            return loading_session("matching events", hud.tokens());
        }
        return kit::status_empty("No events", "Nothing matches this filter.", hud.tokens());
    }
    let (_, ev_marks) = hud.card_marks();
    let tea = hud.tokens();
    let source = hud.timeline_events();
    let list = icedtea::widget::virtual_column(
        hud.timeline_heights(),
        hud.timeline_window(),
        TIMELINE_OVERSCAN,
        None,
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
            let mark = ev_marks.get(&ix).cloned();
            let selected = hud.timeline_focus() == Some(ix);
            let card = closed_list_card(
                event_list_heading(ev, tea),
                event_face(ev, tea),
                card_chips_inline(hud, mark, Some(event_note(ev)), None, None),
                Message::SelectTimeline(ix),
                selected,
                tea,
            );
            column![card, Space::new().height(crate::live::LIST_GAP)].into()
        },
        A11y::new("Timeline", Role::List),
    );
    let more = crate::live::timeline_more_caption(
        hud.timeline_complete(),
        hud.timeline_at_live_end(),
        hud.timeline_loading(),
    );
    let Some(caption) = more else {
        return list;
    };
    column![
        list,
        text(caption).size(typo::META).color(hud.tokens().muted),
    ]
    .spacing(8)
    .height(Length::Fill)
    .into()
}

/// Full-area event body (click a list row; Esc returns to the list at this event).
///
/// Chrome (title + adjacent cards) stays **above** the scroll pane.
fn event_detail_pane(hud: &Hud, ix: i64) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    let Some(ev) = hud.timeline_events().iter().find(|e| e.index == ix) else {
        return column![
            event_detail_chrome(ix, None, None, None, tea),
            loading_session("event", tea),
        ]
        .spacing(10)
        .height(Length::Fill)
        .into();
    };
    let (_, ev_marks) = hud.card_marks();
    let mark = ev_marks.get(&ix).cloned();
    let scroll = icedtea::widget::themed_scroll(
        container(event_body(hud, ev, mark))
            .width(Length::Fill)
            .padding(Padding {
                top: 0.0,
                right: icedtea::chrome::SCROLL_RAIL_WIDTH,
                bottom: 8.0,
                left: 0.0,
            })
            .into(),
        tea,
        A11y::new(format!("Event {ix}"), Role::Group),
        false,
        None,
        None::<fn(scrollable::Viewport) -> Message>,
    );
    let (prev, next) = hud.timeline_detail_adjacent();
    column![event_detail_chrome(ix, Some(ev), prev, next, tea), scroll]
        .spacing(10)
        .height(Length::Fill)
        .into()
}

fn event_detail_chrome(
    ix: i64,
    ev: Option<&TimelineEvent>,
    prev: Option<&TimelineEvent>,
    next: Option<&TimelineEvent>,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let head = ev.map(|e| event_list_heading(e, tea)).unwrap_or_else(|| {
        text(format!("#{ix}"))
            .size(typo::META)
            .font(typo::UI_BOLD)
            .color(tea.text)
            .into()
    });
    column![head, event_neighbor_bar(prev, next, tea)]
        .spacing(4)
        .width(Length::Fill)
        .into()
}

/// Named previous / next cards — same face words as the Timeline list.
fn event_neighbor_bar(
    prev: Option<&TimelineEvent>,
    next: Option<&TimelineEvent>,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let mut row = row![]
        .spacing(12)
        .align_y(Alignment::Center)
        .width(Length::Fill);
    if let Some(ev) = prev {
        row = row.push(neighbor_link(
            ev,
            false,
            Message::TimelineDetailStep(-1),
            tea,
        ));
    }
    row = row.push(Space::new().width(Length::Fill));
    if let Some(ev) = next {
        row = row.push(neighbor_link(ev, true, Message::TimelineDetailStep(1), tea));
    }
    row.into()
}

fn neighbor_link(
    ev: &TimelineEvent,
    ahead: bool,
    msg: Message,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let name = capped_display(&event_card_label(ev), 28);
    let face = if ahead {
        format!("{name} ›")
    } else {
        format!("‹ {name}")
    };
    let hint = if ahead {
        format!("Next {name}")
    } else {
        format!("Previous {name}")
    };
    icedtea::a11y::attach(
        mouse_area(text(face).size(typo::META).color(tea.muted))
            .on_press(msg)
            .into(),
        &A11y::button(hint),
    )
}

fn diff_tab(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    column![
        diff_chrome(hud, tea),
        diff_search(hud),
        diff_split(hud, tea),
    ]
    .spacing(6)
    .height(Length::Fill)
    .into()
}

fn diff_split(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    let files = hud.visible_diff_files();
    let mut list = column![].spacing(0).width(Length::Fill);
    if files.is_empty() {
        list = list.push(kit::status_empty(
            "No file changes",
            "Grok rewind snapshots or search_replace edits for this session.",
            tea,
        ));
    } else {
        let paths: Vec<&str> = files.iter().map(|f| f.path.as_str()).collect();
        let root = crate::diff_tree::file_tree(paths, hud.diff_tree_collapsed());
        let selected = if hud.diff_file().is_empty() {
            None
        } else {
            Some(crate::diff_tree::path_id(hud.diff_file()))
        };
        list = list.push(icedtea::widget::tree_view(
            &root,
            selected,
            None,
            Message::DiffTreeToggle,
            Message::DiffTreeSelect,
            tea,
            A11y::new("Diff files", Role::Tree),
        ));
    }
    let unified = hud
        .current_diff_point()
        .and_then(|p| p.files.iter().find(|f| f.path == hud.diff_file()))
        .map(|f| f.unified.as_str())
        .unwrap_or("");
    let files_pane = container(icedtea::widget::themed_scroll(
        list.into(),
        tea,
        A11y::new("Diff files", Role::List),
        false,
        None,
        None::<fn(_) -> Message>,
    ))
    .width(Length::Fixed(196.0))
    .height(Length::Fill)
    .padding(4)
    .style(move |_| icedtea::style::card(tea, false));
    let hunk_pane = container(icedtea::widget::themed_scroll(
        paint_unified(unified, tea, hud.diff_hit_line()),
        tea,
        A11y::new("Diff hunk", Role::Group),
        false,
        None,
        None::<fn(_) -> Message>,
    ))
    .padding(8)
    .width(Length::Fill)
    .height(Length::Fill)
    .style(move |_| icedtea::style::card(tea, false));
    row![files_pane, hunk_pane]
        .spacing(12)
        .height(Length::Fill)
        .into()
}

fn diff_chrome(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    let mut header = row![].spacing(8).align_y(Alignment::Center);
    if !hud.diff_point_options().is_empty() {
        header = header.push(icedtea::widget::meta(
            "Snapshot",
            tea,
            A11y::new("Snapshot", Role::Header),
        ));
        header = header.push(kit::compact_pick(
            hud.diff_point_options(),
            hud.diff_point_selected(),
            Message::DiffPointPicked,
            tea,
            A11y::new("Snapshot", Role::ComboBox),
        ));
    }
    header = header.push(diff_context_tabs(hud, tea));
    container(
        column![
            header,
            icedtea::widget::themed_scroll(
                diff_context_body(hud, tea),
                tea,
                A11y::new("Diff context body", Role::Group),
                false,
                None,
                None::<fn(_) -> Message>,
            )
        ]
        .spacing(4)
        .height(Length::Fill),
    )
    .padding(Padding {
        top: 2.0,
        right: 8.0,
        bottom: 6.0,
        left: 8.0,
    })
    .height(Length::Fixed(112.0))
    .width(Length::Fill)
    .style(move |_| icedtea::style::card(tea, false))
    .into()
}

fn diff_context_tabs(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    row![
        diff_context_tab(
            "Prompt",
            hud.diff_context() == DiffContext::Prompt,
            Message::DiffContext(DiffContext::Prompt),
            tea,
        ),
        diff_context_tab(
            "Assistant",
            hud.diff_context() == DiffContext::Assistant,
            Message::DiffContext(DiffContext::Assistant),
            tea,
        ),
    ]
    .spacing(0)
    .align_y(Alignment::Center)
    .into()
}

fn diff_context_tab(
    title: &'static str,
    active: bool,
    msg: Message,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    icedtea::a11y::attach(
        button(text(title).size(typo::META))
            .padding([4, 8])
            .style(icedtea::style::tab_style(tea, active))
            .on_press(msg)
            .into(),
        &A11y::new(title, Role::Tab).with_checked(active),
    )
}

fn diff_search(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    kit::search_field(
        "Search files and hunks",
        hud.diff_query(),
        Message::DiffQuery,
        None,
        tea,
        A11y::new("Search diff", Role::TextBox),
        Some(hud.diff_search_id()),
    )
}

fn diff_context_body(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    match hud.diff_context() {
        DiffContext::Prompt => {
            let src = hud
                .current_diff_point()
                .map(|p| p.prompt.as_str())
                .unwrap_or("");
            if src.trim().is_empty() {
                text("(empty)").size(typo::META).color(tea.muted).into()
            } else {
                text(src.to_string())
                    .size(typo::META)
                    .color(tea.text)
                    .into()
            }
        }
        DiffContext::Assistant => {
            let src = hud
                .current_diff_point()
                .map(|p| p.assistant.as_str())
                .unwrap_or("");
            chat_md_body(src, 8000, tea)
        }
    }
}

fn paint_unified(
    unified: &str,
    tea: icedtea::theme::Tokens,
    hit: Option<usize>,
) -> Element<'static, Message> {
    let mut col = column![].spacing(1);
    for line in crate::fuzzy::mark_unified_hit(unified, hit) {
        let hit_row = line.starts_with("> ");
        let core = line.get(2..).unwrap_or(line.as_str());
        let color = if hit_row {
            tea.primary
        } else if core.starts_with('+') && !core.starts_with("+++") {
            tea.success
        } else if core.starts_with('-') && !core.starts_with("---") {
            tea.danger
        } else if core.starts_with("@@") || core.starts_with("+++") || core.starts_with("---") {
            tea.muted
        } else {
            tea.text
        };
        col = col.push(text(line).size(typo::META).font(typo::MONO).color(color));
    }
    col.into()
}

fn findings_tab(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    let findings: &[FindingRow] = &o.findings.findings;
    let tea = hud.body_tokens();
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
            let progress = hud.finding_expand_progress(&id);
            let title = if f.title.is_empty() {
                "Finding".into()
            } else {
                f.title.clone()
            };
            let child = if open || progress > 0.0 {
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
                progress,
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
    let mut card = column![status_chip(
        f.severity.clone(),
        status_tone(&f.severity),
        tea,
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
        .size(typo::BODY)
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
            icedtea::widget::FieldOpts::NONE,
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
            icedtea::widget::FieldOpts::NONE,
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
    // Single Save is a chip (command_bar always paints a leading hairline that
    // reads as a stray "|" with one action). Multi-action edit keeps the bar.
    if editing {
        let nid = hud.note_draft().id.clone();
        let del = if hud.note_delete_armed() == nid {
            "Delete?"
        } else {
            "Delete"
        };
        form = form.push(card_actions(
            vec![
                icedtea::action::Action::new("note.save", save_label, Message::SaveNote),
                icedtea::action::Action::new("note.delete", del, Message::RequestDelete(nid)),
                icedtea::action::Action::new("note.new", "New note", Message::ResetDraft),
            ],
            hud.tokens(),
        ));
    } else {
        form = form.push(chip_btn(save_label.into(), Message::SaveNote, hud.tokens()));
    }

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
                .size(typo::META)
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
            let progress = hud.note_expand_progress(&id);
            let child = if open || progress > 0.0 {
                note_body(hud, n, &body, extras)
            } else {
                prompt_face(&body, hud.tokens())
            };
            col = col.push(expand_card(
                heading,
                child,
                open,
                progress,
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
    let event_type = ev.event_type.clone();
    let tool = ev.tool_name.clone();
    let preview = ev.preview.clone();
    let content = ev.content.clone();
    let raw_body = timeline_body_text(&preview, &content, selected, 240);
    let body = sanitize_console_text(&display_tool_output(&raw_body, &tool));
    let tok = hud.tokens();
    let field_id = ExtractKey::Event(ev.index).id();
    if !selected {
        return render_payload_text(&body, &kind, &event_type, hud, false, &field_id, "");
    }
    let mut col = column![].spacing(8);
    let call_id = ev.tool_call_id.clone();
    if !tool.is_empty() {
        let name_color = if ev.is_error {
            crate::theme::brand_role_color(crate::format::BrandRole::Failed)
        } else {
            match tool_brand_role(&tool, false) {
                Some(role) => crate::theme::brand_role_color(role),
                None => tok.muted,
            }
        };
        col = col.push(
            text(format_tool_display(&tool))
                .size(typo::META)
                .font(typo::UI)
                .color(name_color),
        );
    }
    if !call_id.is_empty() {
        col = col.push(
            text(call_id)
                .size(typo::META)
                .color(tok.muted)
                .font(typo::MONO),
        );
    }
    if kind == "tool" || kind == "tool_result" {
        let (call, result) = paired_tool(hud, ev);
        let path_hint = {
            let mut p = path_hint_from_raw(&call.raw_input);
            if p.is_empty() {
                p = path_hint_from_raw(&result.raw_input);
            }
            if p.is_empty() {
                p = path_hint_from_raw(&ev.raw_input);
            }
            p
        };
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
                    &path_hint,
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
            let out_syn = syntax_for_tool_output(out_tool, &path_hint, &out_body);
            col = col.push(text("Output").size(typo::META).color(tok.muted));
            col = col.push(render_payload_text(
                &out_body,
                &result.kind,
                &result.event_type,
                hud,
                true,
                &format!("event.{}.out", ev.index),
                out_syn,
            ));
        }
    } else {
        // Chat / thought / plan: same paint path as TUI detail (markdown for messages).
        col = col.push(render_payload_text(
            &body,
            &kind,
            &event_type,
            hud,
            true,
            &field_id,
            "",
        ));
    }
    col.into()
}

fn field_body<'a>(
    hud: &'a Hud,
    bind_id: &str,
    field_id: &str,
    value: &str,
    path_hint: &str,
) -> Element<'a, Message> {
    let tea = hud.tokens();
    let syntax = syntax_for_tool_field(field_id, path_hint, value);
    let body = if field_id == "old_string"
        || field_id == "new_string"
        || field_id == "command"
        || field_id == "pattern"
        || crate::format::looks_like_json(value)
        || !syntax.is_empty()
    {
        let syn = if syntax.is_empty() { "txt" } else { syntax };
        code_inset(hud, bind_id, value, syn, tea)
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
    event_type: &str,
    hud: &'a Hud,
    expanded: bool,
    field_id: &str,
    syntax: &str,
) -> Element<'a, Message> {
    let tok = hud.tokens();
    let trimmed = body.trim();
    let paint = body_paint_for(kind, event_type, trimmed, expanded);
    if paint == BodyPaint::Empty {
        return text("empty").size(typo::META).color(tok.muted).into();
    }
    let max = if expanded { 12_000 } else { 400 };
    let cut = capped_display(body, max);
    if !expanded {
        return text(cut)
            .size(typo::META)
            .font(typo::UI)
            .color(tok.muted)
            .into();
    }
    match paint {
        BodyPaint::Json => code_inset(hud, field_id, &cut, "json", hud.tokens()),
        BodyPaint::Code => {
            let syn = if syntax.is_empty() {
                syntax_for_tool_output("", "", &cut)
            } else {
                syntax
            };
            let syn = if syn.is_empty() { "txt" } else { syn };
            code_inset(hud, field_id, &cut, syn, hud.tokens())
        }
        BodyPaint::Image => tool_image(trimmed, hud.tokens()),
        BodyPaint::Markdown => {
            // icedtea markdown_view (TUI uses Rich Markdown). Yank still uses
            // bound plain text / extract_event via y.
            let md = if is_chat_message(kind, event_type) {
                chat_md_body(body, max, hud.tokens())
            } else {
                md_body(body, max, hud.tokens())
            };
            if is_chat_message(kind, event_type) || kind == "subagent" {
                inset_body(md, hud)
            } else {
                md
            }
        }
        BodyPaint::Plain | BodyPaint::Empty => {
            // Prefer real highlighting when we still know a language (e.g. file path).
            if !syntax.is_empty() && (kind == "tool" || kind == "tool_result") {
                return code_inset(hud, field_id, &cut, syntax, hud.tokens());
            }
            let plain = if kind == "thought" {
                text(cut)
                    .size(typo::META)
                    .font(typo::UI)
                    .color(tok.muted)
                    .into()
            } else if kind == "tool" || kind == "tool_result" {
                // Shell stdout / non-source tool bodies: monospaced like TUI.
                select_bound(
                    hud,
                    field_id.to_string(),
                    &cut,
                    tok,
                    icedtea::typo::FontFace::Mono,
                )
            } else {
                select_bound(
                    hud,
                    field_id.to_string(),
                    &cut,
                    tok,
                    icedtea::typo::FontFace::Ui,
                )
            };
            plain
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
        icedtea::widget::TooltipAnchor::Follow,
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
        icedtea::widget::TooltipAnchor::Follow,
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
    fn plain_card_text_strips_markdown_markers() {
        assert_eq!(
            plain_card_text("You are an **adversarial** verifier"),
            "You are an adversarial verifier"
        );
        assert_eq!(plain_card_text("see `code` and __x__"), "see code and x");
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
    fn event_heading_has_type_turn_and_time() {
        let ev = TimelineEvent {
            index: 12,
            event_type: "user_message_chunk".into(),
            type_label: "user message chunk".into(),
            kind: "user".into(),
            time: "10:32".into(),
            turn_index: Some(2),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title_meta(&ev), "turn 2 · 10:32");
        let no_turn = TimelineEvent {
            index: 12,
            kind: "user".into(),
            time: "10:32".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title_meta(&no_turn), "10:32");
        let bare = TimelineEvent {
            index: 3,
            kind: "user".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title_meta(&bare), "");
        assert_eq!(
            event_type_human(&ev),
            "user message chunk",
            "human type sits on the heading with #index"
        );
        assert_eq!(event_title_meta(&ev), "turn 2 · 10:32");
        let painted = event_type_paint(&ev).expect("type");
        assert_eq!(painted.0, "user message chunk");
        assert_eq!(painted.1, BrandRole::Cream);
        let _ = event_list_heading(&ev, tea());
        let tool = TimelineEvent {
            index: 4,
            event_type: "tool_call".into(),
            type_label: "tool call".into(),
            kind: "tool".into(),
            tool_name: "read_file".into(),
            preview: "src/app.rs".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_tool_role(&tool), BrandRole::Cream);
        let _ = event_face(&tool, tea());
        let prod = include_str!("view.rs")
            .split("#[cfg(test)]")
            .next()
            .expect("prod");
        let body = prod
            .split("fn event_body")
            .nth(1)
            .expect("event_body")
            .split("fn finding_jump")
            .next()
            .expect("body");
        assert!(
            !body.contains("event_type_human"),
            "detail body must not repeat the chrome type line"
        );
        assert!(prod.contains("fn event_detail_chrome"));
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
        let prod = include_str!("view.rs")
            .split("#[cfg(test)]")
            .next()
            .expect("prod");
        assert!(prod.contains("fn loading_session"));
        assert!(prod.contains("widget::progress("));
        assert!(!prod.contains("Loading events…"));
        assert!(!prod.contains("Loading matching events…"));
        assert!(!prod.contains("Loading event…"));
    }

    #[test]
    fn timeline_filter_and_empty_list_build_from_hud() {
        let hud = Hud::default();
        assert!(hud.sessions().is_empty());
        let _ = timeline_filter(&hud);
        let _ = session_picker_at(&hud, 400.0);
        let _ = layout(&hud);
        let src = include_str!("view.rs");
        assert!(
            src.contains("Search events…"),
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
        assert!(
            filter_src.contains("themed_switch"),
            "live Tail switch sits on the Timeline filter bar"
        );
        assert!(src.contains("kit::pane_tabs"), "session-gated tabs");
    }

    #[test]
    fn code_inset_pretty_prints_json_through_icedtea() {
        let mut hud = Hud::default();
        hud.bind_field("code.json", r#"{ "a": 1 }"#);
        hud.bind_field("code.plain", "not json");
        let _ = code_inset(&hud, "code.json", "", "json", tea());
        let _ = code_inset(&hud, "code.plain", "", "py", tea());
        let _ = code_inset(&hud, "missing", "fallback body", "txt", tea());
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
    fn session_status_tones_stay_distinct_and_readable() {
        assert_eq!(tone_variant("complete"), Variant::Success);
        assert_eq!(tone_variant("running"), Variant::Warning);
        assert_eq!(tone_variant("awaiting"), Variant::Quiet);
        assert_eq!(tone_variant("ending"), Variant::Quiet);
        assert_eq!(tone_variant("cancelled"), Variant::Danger);
        for name in ["dark", "light"] {
            let tok = icedtea::theme::named(name).tokens;
            let run = crate::theme::ink_on(tok.warning, tok.surface);
            let done = crate::theme::ink_on(tok.success, tok.surface);
            assert_ne!(run, done, "{name} running vs complete");
            assert!(
                crate::theme::contrast_ratio(run, tok.surface) >= 4.5,
                "{name} running"
            );
            assert!(
                crate::theme::contrast_ratio(done, tok.surface) >= 4.5,
                "{name} complete"
            );
        }
        let _ = status_chip("complete", "complete", tea());
        let _ = status_chip("running", "running", tea());
    }

    #[test]
    fn hud_markdown_uses_overlay_type_scale() {
        let s = hud_md_settings(tea());
        assert_eq!(f32::from(s.text_size), typo::META as f32);
        assert_eq!(f32::from(s.h1_size), typo::TITLE as f32);
        assert_eq!(f32::from(s.h2_size), typo::BODY as f32);
        assert!(f32::from(s.h1_size) < typo::PAGE as f32);
    }

    #[test]
    fn session_picker_is_spotlight_not_list_detail_rail() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod source");
        assert!(prod.contains("fn session_picker"));
        assert!(prod.contains("browse_mode()"));
        assert!(prod.contains("fn browse_session_bar"));
        assert!(prod.contains("Message::SessionsHome"));
        assert!(prod.contains("fn status_chip"));
        assert!(prod.contains("BadgeSize::Small"));
        assert!(prod.contains("fn session_list_card"));
        assert!(prod.contains("fn session_state_row"));
        assert!(prod.contains("widget::virtual_column"));
        assert!(!prod.contains("RowFace::Card"));
        assert!(!prod.contains("fn tea_two_line"));
        assert!(!prod.contains("fn tea_list_view"));
        assert!(!prod.contains("SESSION_LIST_W"));
        assert!(!prod.contains("pattern::list_detail"));
        assert!(prod.contains("widget::rule_h"));
        assert!(prod.contains("widget::tooltip_wrap"));
        assert!(prod.contains("kit::compact_pick"));
        assert!(prod.contains("icedtea::widget::themed_text_input"));
        assert!(
            prod.contains("icedtea::widget::highlighted_code"),
            "tool code panes must use iced highlighter, not plain mono code_block"
        );
        assert!(prod.contains("icedtea::widget::selectable"));
        // Overview KV via kit (icedtea value_field + FORM_LABEL gutter).
        assert!(prod.contains("kit::labeled_value"));
        assert!(prod.contains("kit::labeled_plain"));
        assert!(prod.contains("kit::context_progress"));
        assert!(prod.contains("kit::pane_tabs"));
        assert!(prod.contains("kit::search_field"));
        assert!(prod.contains("kit::status_footer"));
        assert!(prod.contains("kit::help_modal"));
        assert!(prod.contains("kit::status_empty"));
        assert!(prod.contains("help_open()"));
        assert!(prod.contains("overview_fields"));
        let overview = prod
            .split("fn overview_tab")
            .nth(1)
            .expect("overview_tab")
            .split("fn intern_md")
            .next()
            .expect("overview body");
        assert!(overview.contains("session_state_row("));
        assert!(!overview.contains("\"{} · {} · {}\""));
        assert!(prod.contains("fn select_bound"));
        assert!(prod.contains("event.{}.in.{}"));
        assert!(prod.contains("icedtea::widget::image_slot"));
        assert!(prod.contains("icedtea::widget::progress"));
        assert!(prod.contains("icedtea::pattern::status_page"));
        assert!(prod.contains("icedtea::widget::info_bar"));
        assert!(prod.contains("fn hud_md_settings"));
        assert!(prod.contains("iced::widget::markdown::view"));
        assert!(prod.contains("fn diff_chrome"));
        assert!(prod.contains("fn diff_context_body"));
        assert!(prod.contains("fn diff_split"));
        assert!(prod.contains("widget::tree_view"));
        assert!(prod.contains("fn diff_search"));
        assert!(prod.contains("Message::DiffPointPicked"));
        assert!(prod.contains("chat_md_body(src, 8000, tea)"));
        assert!(prod.contains("icedtea::motion::overlay"));
        assert!(prod.contains("Slide::Up"));
        assert!(prod.contains("page_slide()"));
        assert!(prod.contains("fn page_body"));
        assert!(prod.contains("overlay_moving()"));
        assert!(prod.contains("page_moving()"));
        assert!(prod.contains("fn expand_card"));
        assert!(prod.contains("fn card_actions"));
        assert!(prod.contains("fn card_chips"));
        assert!(prod.contains("fn command_end"));
        assert!(prod.contains("Add note"));
        // Overview path is selectable; no in-pane Copy path button.
        assert!(!prod.contains("fn overview_commands"));
        assert!(prod.contains("format!(\"f{}\""));
        assert!(prod.contains("format!(\"n{}\""));
        assert!(!prod.contains("Tab fields"));
        assert!(!prod.contains("Ctrl+1–5"));
        assert!(!prod.contains("hotkey_hint()"));
        assert!(prod.contains("themed_button("));
        assert!(prod.contains("Variant::Chip"));
        assert!(!prod.contains("themed_button_sized"));
        assert!(prod.contains("fn jump_control"));
        assert!(prod.contains("Go to Timeline"));
        assert!(!prod.contains("struct JumpIcon"));
        assert!(!prod.contains("chip_btn(\"Timeline\""));
        assert!(prod.contains("pattern::command_bar"));
        assert!(prod.contains("TURNS_OVERSCAN"));
        assert!(prod.contains("widget::virtual_column"));
        assert!(prod.contains("turns_tab(hud)"));
        assert!(prod.contains("fn session_list_card"));
        assert!(!prod.contains("context_progress") || prod.contains("kit::context_progress"));
        assert!(prod.contains("pattern::context_menu"));
        assert!(prod.contains("stack![busy]"));
        assert!(prod.contains("fn turn_note"));
        assert!(!prod.contains("command_palette_view"));
        assert!(prod.contains("fn event_body"));
        assert!(!prod.contains("time_picker"));
        assert!(!prod.contains("fn drawer"));
        assert!(!prod.contains("fn disclosure"));
        assert!(prod.contains("fn select_bound"));
        assert!(prod.contains("fn turn_list_card"));
        assert!(prod.contains("fn closed_turn_face"));
        assert!(prod.contains("Search events…"));
        assert!(prod.contains("Search turns"));
        assert!(!prod.contains("Session events"));
        assert!(prod.contains("fn prompt_face"));
        assert!(!prod.contains("visual_lines("));
        assert!(!prod.contains(".height(height)"));
        assert!(prod.contains("matched in {}:"));
        assert!(prod.contains("brand_role_color"));
        assert!(!prod.contains("accordion_view"));
        assert!(prod.contains("widget::expander"));
        assert!(prod.contains("finding_expand_progress"));
        assert!(prod.contains("note_expand_progress"));
        assert!(!prod.contains("if open { 1.0 } else { 0.0 }"));
        assert!(prod.contains("Peek::Lines(2)"));
        assert!(prod.contains("fn closed_list_card"));
        assert!(prod.contains("fn event_detail_pane"));
        assert!(prod.contains("fn event_neighbor_bar"));
        assert!(prod.contains("fn neighbor_link"));
        assert!(prod.contains("fn event_card_label"));
        assert!(prod.contains("fn event_list_heading"));
        assert!(prod.contains("fn event_type_paint"));
        assert!(prod.contains("fn label_badge"));
        assert!(prod.contains("fn brand_variant"));
        let heading = prod
            .split("fn event_list_heading")
            .nth(1)
            .expect("heading")
            .split("fn event_face")
            .next()
            .expect("heading body");
        assert!(heading.contains("label_badge"));
        let face = prod
            .split("fn event_face")
            .nth(1)
            .expect("face")
            .split("fn event_body")
            .next()
            .expect("face body");
        assert!(face.contains("label_badge"));
        assert!(!face.contains("id_font"));
        assert!(!prod.contains("{at} of {n}"));
        assert!(prod.contains("footer_table_for(hud.key_scope(), hud.key_overlay())"));
        assert!(!prod.contains("chip_btn(\"Back\""));
        assert!(!prod.contains("is_timeline_expanded"));
        assert!(!prod.contains("TurnExpand"));
        assert!(!prod.contains("fn turn_body"));
        assert!(prod.contains("FindingExpand"));
        assert!(prod.contains("NoteExpand"));
    }
}
